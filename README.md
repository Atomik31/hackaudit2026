# Agent IA AWS — Dashboard Streamlit sécurisé par Cognito

Interface Streamlit pour dialoguer avec un agent hébergé sur un **Harness
Amazon Bedrock AgentCore**, protégée par le flux d'authentification suivant :

1. L'utilisateur se connecte (identifiant / mot de passe) contre un
   **Cognito User Pool** via le protocole **SRP** — le mot de passe n'est
   jamais transmis en clair.
2. Un **MFA** (TOTP ou SMS) est demandé si le pool l'exige.
3. Les jetons JWT obtenus sont échangés auprès d'un **Cognito Identity
   Pool**, qui appelle `sts:AssumeRoleWithWebIdentity` et renvoie des
   **identifiants AWS temporaires** rattachés à un rôle IAM — jamais de clé
   statique.
4. Ce rôle IAM est scoppé à la seule action
   `bedrock-agentcore:InvokeHarness` sur l'ARN du Harness ciblé.
5. Ces identifiants (renouvelés automatiquement, ~1h) signent en SigV4
   l'appel `InvokeHarness`.

> Une ressource `arn:...:harness/<id>` est invoquée via **`InvokeHarness`**
> (format de messages façon Bedrock Converse, réponse en flux d'événements),
> pas via `InvokeAgentRuntime` (réservé aux runtimes autonomes de type
> `arn:...:runtime/<id>`, qui existe en parallèle pour chaque Harness mais
> ne s'invoque pas directement).

## Arborescence

```
app.py                 # UI Streamlit (login, MFA, chat)
src/config.py           # chargement de la configuration
src/cognito_auth.py      # authentification User Pool (SRP + MFA)
src/aws_identity.py       # échange JWT -> identifiants AWS temporaires
src/agent_client.py        # appel InvokeHarness
```

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
créer un fichier .env à la racine (voir le tableau ci-dessous pour les variables)
streamlit run app.py
```

## Configuration (`.env` ou `.streamlit/secrets.toml`)

| Variable | Description |
|---|---|
| `AWS_REGION` | Région du User Pool / Identity Pool / Harness |
| `COGNITO_USER_POOL_ID` | ID du User Pool |
| `COGNITO_APP_CLIENT_ID` | App client utilisé par l'app (voir ci-dessous) |
| `COGNITO_APP_CLIENT_SECRET` | Vide si l'app client est public |
| `COGNITO_IDENTITY_POOL_ID` | ID de l'Identity Pool |
| `AGENT_HARNESS_ARN` | ARN du Harness AgentCore (`arn:...:harness/<id>`) |
| `AGENT_HARNESS_QUALIFIER` | Optionnel (alias de version) |

L'application tourne côté serveur (pas dans le navigateur), donc un app
client **avec secret** (`generate_secret`) est recommandé pour plus de
robustesse — le secret ne sort jamais du process Streamlit.

## Provisionnement AWS (une fois)

### 1. User Pool avec MFA obligatoire

```bash
aws cognito-idp create-user-pool \
  --pool-name hackaudit-agent-dashboard \
  --mfa-configuration ON \
  --enabled-mfas SOFTWARE_TOKEN_MFA \
  --policies '{"PasswordPolicy":{"MinimumLength":12,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":true}}' \
  --user-pool-add-ons '{"AdvancedSecurityMode":"ENFORCED"}'

aws cognito-idp create-user-pool-client \
  --user-pool-id <USER_POOL_ID> \
  --client-name streamlit-dashboard \
  --generate-secret \
  --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH
```

> Onboarding d'un utilisateur : `admin-create-user` avec mot de passe
> temporaire, puis lors de sa toute première connexion, l'utilisateur
> définit son mot de passe permanent et enrôle son TOTP (à faire une fois,
> via la Hosted UI ou un écran d'onboarding dédié — cette app suppose que le
> compte est déjà provisionné et gère uniquement les connexions récurrentes
> avec MFA déjà enrôlé).

### 2. Identity Pool + rôle IAM scoppé

```bash
aws cognito-identity create-identity-pool \
  --identity-pool-name hackaudit_agent_dashboard \
  --no-allow-unauthenticated-identities \
  --cognito-identity-providers ProviderName=cognito-idp.<REGION>.amazonaws.com/<USER_POOL_ID>,ClientId=<APP_CLIENT_ID>,ServerSideTokenCheck=true
```

Rôle IAM (trust policy — assumable uniquement via cette Identity Pool, pour
des identités authentifiées) :

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "cognito-identity.amazonaws.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"cognito-identity.amazonaws.com:aud": "<IDENTITY_POOL_ID>"},
      "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "authenticated"}
    }
  }]
}
```

Politique inline (moindre privilège, un seul Harness) :

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock-agentcore:InvokeHarness",
    "Resource": "arn:aws:bedrock-agentcore:us-east-1:377152275173:harness/Agent_RAG-9mya02G5gI"
  }]
}
```

Rattachement du rôle par défaut :

```bash
aws cognito-identity set-identity-pool-roles \
  --identity-pool-id <IDENTITY_POOL_ID> \
  --roles authenticated=<ROLE_ARN>
```

### 3. (optionnel) Rôles différenciés par groupe Cognito

Créer des groupes (`admin`, `user`), puis du role mapping basé sur la
revendication `cognito:groups` :

```bash
aws cognito-idp create-group --user-pool-id <USER_POOL_ID> --group-name admin
aws cognito-idp create-group --user-pool-id <USER_POOL_ID> --group-name user

aws cognito-identity set-identity-pool-roles \
  --identity-pool-id <IDENTITY_POOL_ID> \
  --roles authenticated=<ROLE_USER_ARN> \
  --role-mappings "cognito-idp.<REGION>.amazonaws.com/<USER_POOL_ID>:<APP_CLIENT_ID>={Type=Rules,AmbiguousRoleResolution=Deny,RulesConfiguration={Rules=[{Claim=cognito:groups,MatchType=Contains,Value=admin,RoleARN=<ROLE_ADMIN_ARN>}]}}"
```

## Sécurité

- Aucune clé AWS statique : uniquement des identifiants temporaires dérivés
  de la session Cognito, renouvelés automatiquement (~1h) tant que
  l'utilisateur reste connecté.
- Le rôle IAM assumé est scoppé au strict minimum (une seule action, un seul
  ARN de ressource).
- MFA obligatoire au niveau du pool (`--mfa-configuration ON`) +
  `AdvancedSecurityMode=ENFORCED` (détection de connexions à risque).
- Les erreurs d'authentification sont volontairement génériques
  (« Identifiants incorrects ») pour éviter l'énumération de comptes.
- Ne commitez jamais `.env` ni `.streamlit/secrets.toml` (déjà exclus via
  `.gitignore`).
