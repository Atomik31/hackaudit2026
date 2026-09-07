"""Dashboard Streamlit pour un agent IA connecté à AWS (Bedrock AgentCore),
sécurisé par Amazon Cognito.

Flux implémenté :
  1. Connexion (formulaire) -> Cognito User Pool via SRP (le mot de passe
     n'est jamais transmis en clair) + MFA obligatoire (TOTP ou SMS).
  2. En cas de succès, les jetons JWT Cognito sont échangés contre des
     identifiants AWS temporaires via un Cognito Identity Pool
     (sts:AssumeRoleWithWebIdentity), rattachés à un rôle IAM scoppé à
     `bedrock-agentcore:InvokeHarness` sur le Harness cible.
  3. Ces identifiants (jamais des clés statiques) signent en SigV4 les
     appels à l'agent, et sont renouvelés automatiquement tant que la
     session Cognito reste valide.
"""

from __future__ import annotations

import jwt
import streamlit as st

from src import cognito_auth
from src.aws_identity import exchange_id_token_for_credentials
from src.config import load_config
from src.s3_deposit import list_documents, upload_document
from src.sub_agents import SubAgentError, extract_document, verify_content

st.set_page_config(
    page_title="Agent IA HACKAUDIT 2026 AWS",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# --------------------------------------------------------------------------
# État de session
# --------------------------------------------------------------------------

def _init_state() -> None:
    defaults = {
        "auth_stage": "login",  # login | mfa | authenticated
        "cognito_user": None,
        "mfa_status": None,
        "mfa_tokens": None,
        "pending_username": None,
        "aws_credentials": None,
        "messages": [],
        "auth_error": None,
        "active_client_slug": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _reset_session() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    _init_state()


# --------------------------------------------------------------------------
# Identifiants AWS : dérivation / rafraîchissement
# --------------------------------------------------------------------------

def _get_valid_credentials(config):
    cognito_user = st.session_state.cognito_user

    try:
        cognito_auth.ensure_fresh_tokens(cognito_user)
    except Exception:
        st.session_state.auth_error = "Votre session a expiré, veuillez vous reconnecter."
        st.session_state.auth_stage = "login"
        st.rerun()

    creds = st.session_state.aws_credentials
    if creds is None or creds.is_expiring_soon():
        creds = exchange_id_token_for_credentials(
            region=config.cognito_region,
            identity_pool_id=config.identity_pool_id,
            user_pool_id=config.user_pool_id,
            id_token=cognito_user.id_token,
        )
        st.session_state.aws_credentials = creds
    return creds


# --------------------------------------------------------------------------
# Écrans
# --------------------------------------------------------------------------

def _render_login(config) -> None:
    st.markdown(
        "<h1 style='text-align:center'>Agent IA HackAudit 2026 — accès sécurisé AWS</h1>"
        "<p style='text-align:center; color:var(--text-color-secondary, gray);'>"
        "Authentification Amazon Cognito (SRP + MFA) — aucune clé AWS statique.</p>",
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        if st.session_state.auth_error:
            st.error(st.session_state.auth_error)
            st.session_state.auth_error = None

        with st.form("login_form"):
            username = st.text_input("Identifiant")
            password = st.text_input("Mot de passe", type="password")
            submitted = st.form_submit_button("Se connecter", use_container_width=True)

        if submitted:
            if not username or not password:
                st.error("Veuillez renseigner l'identifiant et le mot de passe.")
                return
            with st.spinner("Authentification en cours..."):
                outcome = cognito_auth.start_login(
                    user_pool_id=config.user_pool_id,
                    app_client_id=config.app_client_id,
                    region=config.cognito_region,
                    app_client_secret=config.app_client_secret,
                    username=username,
                    password=password,
                )
            _handle_login_outcome(config, outcome, username)


def _render_mfa(config) -> None:
    st.title("🔐 Vérification en deux étapes")
    label = "Code de votre application d'authentification (TOTP)"
    if st.session_state.mfa_status == cognito_auth.AuthStatus.MFA_SMS:
        label = "Code reçu par SMS"

    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        if st.session_state.auth_error:
            st.error(st.session_state.auth_error)
            st.session_state.auth_error = None

        with st.form("mfa_form"):
            code = st.text_input(label, max_chars=6)
            submitted = st.form_submit_button("Valider", use_container_width=True)
        cancel = st.button("Annuler et revenir à la connexion")

    if cancel:
        _reset_session()
        st.rerun()

    if submitted:
        if not code:
            st.error("Veuillez saisir le code.")
            return
        with st.spinner("Vérification du code..."):
            outcome = cognito_auth.submit_mfa_code(
                cognito_user=st.session_state.cognito_user,
                status=st.session_state.mfa_status,
                mfa_tokens=st.session_state.mfa_tokens,
                code=code.strip(),
            )
        _handle_login_outcome(config, outcome, st.session_state.pending_username)


def _handle_login_outcome(config, outcome: cognito_auth.LoginOutcome, username: str | None) -> None:
    if outcome.status == cognito_auth.AuthStatus.SUCCESS:
        st.session_state.cognito_user = outcome.cognito_user
        st.session_state.pending_username = username
        with st.spinner("Récupération des identifiants AWS temporaires..."):
            try:
                st.session_state.aws_credentials = exchange_id_token_for_credentials(
                    region=config.cognito_region,
                    identity_pool_id=config.identity_pool_id,
                    user_pool_id=config.user_pool_id,
                    id_token=outcome.cognito_user.id_token,
                )
            except Exception as exc:  # noqa: BLE001
                st.session_state.auth_error = (
                    "Connexion Cognito réussie mais l'échange contre des "
                    "identifiants AWS a échoué. Vérifiez la configuration de "
                    "l'Identity Pool et le role mapping."
                )
                st.session_state.auth_stage = "login"
                st.rerun()
                return
        st.session_state.auth_stage = "authenticated"
        st.rerun()

    elif outcome.status in (cognito_auth.AuthStatus.MFA_SOFTWARE_TOKEN, cognito_auth.AuthStatus.MFA_SMS):
        st.session_state.cognito_user = outcome.cognito_user
        st.session_state.mfa_status = outcome.status
        st.session_state.mfa_tokens = outcome.mfa_tokens
        st.session_state.pending_username = username
        st.session_state.auth_stage = "mfa"
        st.rerun()

    else:
        st.session_state.auth_error = outcome.error_message or "Échec de l'authentification."
        st.rerun()


def _decode_claims(id_token: str) -> dict:
    try:
        return jwt.decode(id_token, options={"verify_signature": False})
    except Exception:  # noqa: BLE001
        return {}


def _resolve_role(config, claims: dict) -> tuple[str | None, list[str]]:
    """Déduit le rôle applicatif (client/commissaire) des groupes Cognito.

    Le rôle IAM (donc les vraies permissions AWS) est déjà déterminé côté
    Identity Pool par ces mêmes groupes — cette fonction ne fait que décider
    quel écran afficher, elle n'accorde aucun accès par elle-même.

    Un client peut appartenir à un groupe donnant accès à plusieurs espaces
    de travail (ex. un client qui gère plusieurs entreprises) : on renvoie
    alors la liste complète des slugs accessibles.
    """
    groups = claims.get("cognito:groups", [])
    if "commissaire" in groups:
        return "commissaire", []
    for group in groups:
        if group.startswith("client-"):
            key = group[len("client-") :]
            if key in config.client_groups:
                return "client", config.client_groups[key]
    return None, []


def _render_header() -> None:
    _, col_button = st.columns([10, 1])
    with col_button:
        if st.button("Déconnexion", use_container_width=True):
            _reset_session()
            st.rerun()


def _render_deposit(config, credentials, workspace_slugs: list[str]) -> None:
    st.title("Dépôt de documents")

    if len(workspace_slugs) > 1:
        selected_slug = st.selectbox(
            "Entreprise concernée par ce dépôt",
            workspace_slugs,
            format_func=lambda s: config.workspaces[s].label,
            key="selected_deposit_slug",
        )
    else:
        selected_slug = workspace_slugs[0]

    client = config.workspaces[selected_slug]
    st.subheader(client.label)
    st.caption(
        "Ces documents sont déposés dans un espace strictement réservé à cette "
        "entreprise : aucun autre dossier client n'y a accès."
    )

    uploaded_files = st.file_uploader("Documents demandés par le commissaire aux comptes", accept_multiple_files=True)
    if st.button("Déposer", disabled=not uploaded_files):
        with st.spinner("Dépôt en cours..."):
            try:
                for uploaded in uploaded_files:
                    upload_document(
                        credentials=credentials,
                        region=client.bucket_region,
                        bucket=client.bucket,
                        file_name=uploaded.name,
                        file_bytes=uploaded.getvalue(),
                    )
                st.success(f"{len(uploaded_files)} document(s) déposé(s).")
            except Exception:  # noqa: BLE001
                st.error("Le dépôt a échoué. Réessayez ou contactez un administrateur.")

    st.subheader("Documents déjà déposés")
    try:
        documents = list_documents(credentials, client.bucket_region, client.bucket)
    except Exception:  # noqa: BLE001
        documents = []
        st.error("Impossible de lister les documents déposés.")

    if not documents:
        st.caption("Aucun document déposé pour le moment.")
    else:
        for doc in documents:
            st.markdown(f"- `{doc.key}` — {doc.size_bytes // 1024} Ko — déposé le {doc.last_modified}")


def _render_analysis(config, credentials) -> None:
    st.title("Agent IA HackAudit 2026 — Analyse")

    slugs = list(config.workspaces.keys())
    selected_slug = st.selectbox(
        "Dossier client en cours de mission",
        slugs,
        format_func=lambda s: config.workspaces[s].label,
        key="selected_client_slug",
    )
    if st.session_state.active_client_slug != selected_slug:
        st.session_state.active_client_slug = selected_slug
        st.session_state.messages = []

    client = config.workspaces[selected_slug]
    st.caption(
        "Sous-agents : Extraction (lecture fiable, OCR/Textract si besoin) → "
        "Vérification (analyse du contenu déjà extrait). Orchestrés par l'application, "
        "pas par une décision du modèle — le contenu soumis à l'analyse est donc "
        "toujours réel, jamais halluciné."
    )

    try:
        documents = list_documents(credentials, client.bucket_region, client.bucket)
    except Exception:  # noqa: BLE001
        documents = []
        st.error("Impossible de lister les documents déposés par ce client.")

    if documents:
        st.caption("Documents disponibles pour analyse : " + ", ".join(f"`{d.key}`" for d in documents))
    else:
        st.caption("Aucun document déposé par ce client pour le moment.")

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input(f"Ex. : Fais-moi un rapport de pré-audit sur {documents[0].key if documents else '...'}")
    if not prompt:
        return

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        answer = None
        status = st.empty()
        try:
            credentials = _get_valid_credentials(config)

            extracted_sections = []
            for index, doc in enumerate(documents, start=1):
                status.markdown(f"*Extraction du document {index}/{len(documents)} : `{doc.key}`...*")
                text = extract_document(
                    credentials, config.resource_region, config.gateway_url,
                    config.gateway_protocol_version, client.extract_tool, doc.key,
                )
                extracted_sections.append(f"### Document : {doc.key}\n{text}")

            if not extracted_sections:
                status.empty()
                st.error("Aucun document déposé à analyser pour ce client.")
            else:
                status.markdown("*Vérification et synthèse en cours...*")
                combined_content = "\n\n".join(extracted_sections) + f"\n\n### Demande du commissaire\n{prompt}"
                answer = verify_content(
                    credentials, config.resource_region, config.gateway_url,
                    config.gateway_protocol_version, client.verify_tool, combined_content,
                )
                status.empty()
                st.markdown(answer)
        except SubAgentError as exc:
            status.empty()
            st.error(f"Échec d'un sous-agent : {exc}")
        except Exception:  # noqa: BLE001
            status.empty()
            st.error("Une erreur inattendue est survenue lors de l'analyse.")
        if answer:
            st.session_state.messages.append({"role": "assistant", "content": answer})


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------

def main() -> None:
    _init_state()
    config = load_config()

    if st.session_state.auth_stage == "login":
        _render_login(config)
    elif st.session_state.auth_stage == "mfa":
        _render_mfa(config)
    else:
        credentials = _get_valid_credentials(config)
        claims = _decode_claims(st.session_state.cognito_user.id_token)
        role, workspace_slugs = _resolve_role(config, claims)
        _render_header()

        if role == "client":
            _render_deposit(config, credentials, workspace_slugs)
        elif role == "commissaire":
            _render_analysis(config, credentials)
        else:
            st.error(
                "Aucun rôle n'est assigné à ce compte (ni groupe `commissaire`, ni "
                "groupe `client-<dossier>`). Contactez un administrateur."
            )


if __name__ == "__main__":
    main()
