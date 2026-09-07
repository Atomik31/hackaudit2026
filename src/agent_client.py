"""Étape 5 du flux : invocation du Harness Bedrock AgentCore avec les
identifiants AWS temporaires (signature SigV4 gérée par boto3), en respectant
le principe de moindre privilège porté par le rôle IAM assumé
(bedrock-agentcore:InvokeHarness sur ce seul ARN).

Une ressource "harness" (arn:...:harness/<id>) ne s'invoque pas via
InvokeAgentRuntime (réservé aux runtimes autonomes) mais via InvokeHarness,
qui prend une liste de `messages` façon Bedrock Converse et renvoie un flux
d'événements (message_start / content_block_delta / message_stop / ...).
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
from botocore.exceptions import ClientError

from src.aws_identity import TemporaryAwsCredentials

# Contrainte imposée par l'API AgentCore pour runtimeSessionId : longueur
# 33-100, uniquement [a-zA-Z0-9-_], doit commencer par un caractère alphanum.
_MIN_SESSION_ID_LENGTH = 33


class AgentInvocationError(Exception):
    pass


def stream_agent_response(
    credentials: TemporaryAwsCredentials,
    region: str,
    harness_arn: str,
    prompt: str,
    session_id: str,
    qualifier: str | None = None,
    allowed_tools: list[str] | None = None,
    tool_usage: dict | None = None,
) -> Iterator[str]:
    """Yield les morceaux de texte au fil de l'inférence (SSE-like), pour un
    affichage progressif côté UI plutôt que d'attendre la réponse complète.

    `allowed_tools` restreint les outils que l'orchestrateur peut appeler
    pendant cette invocation précise (ex. les seuls outils Extraction/
    Vérification du client en cours de mission) : le Harness a accès à tous
    les outils du Gateway par défaut, cette restriction par appel est ce qui
    garantit qu'une session ne peut techniquement pas interroger les
    documents d'un autre client.

    `tool_usage`, si fourni, est rempli avec `{"used": bool}` : le modèle
    peut produire du texte plausible sans jamais appeler d'outil réel
    (hallucination) — l'appelant doit vérifier ce drapeau avant de faire
    confiance à la réponse pour un usage d'audit.
    """
    if tool_usage is not None:
        tool_usage["used"] = False
    if len(session_id) < _MIN_SESSION_ID_LENGTH:
        raise AgentInvocationError("Identifiant de session runtime invalide.")

    client = boto3.client(
        "bedrock-agentcore",
        region_name=region,
        aws_access_key_id=credentials.access_key_id,
        aws_secret_access_key=credentials.secret_access_key,
        aws_session_token=credentials.session_token,
    )

    kwargs: dict[str, object] = {
        "harnessArn": harness_arn,
        "runtimeSessionId": session_id,
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
    }
    if qualifier:
        kwargs["qualifier"] = qualifier
    if allowed_tools:
        kwargs["allowedTools"] = allowed_tools

    try:
        response = client.invoke_harness(**kwargs)
    except ClientError as exc:
        raise AgentInvocationError(_friendly_invoke_error(exc)) from exc

    got_any_text = False
    for event in response["stream"]:
        start = event.get("contentBlockStart", {}).get("start", {})
        if "toolUse" in start and tool_usage is not None:
            tool_usage["used"] = True
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "toolUse" in delta and tool_usage is not None:
                tool_usage["used"] = True
            text = delta.get("text")
            if text:
                got_any_text = True
                yield text
        elif "validationException" in event:
            raise AgentInvocationError(
                f"Requête invalide envoyée à l'agent : {event['validationException'].get('message', '')}"
            )
        elif "internalServerException" in event:
            raise AgentInvocationError("Erreur interne côté agent, réessayez.")
        elif "runtimeClientError" in event:
            raise AgentInvocationError(
                f"Erreur de l'agent : {event['runtimeClientError'].get('message', '')}"
            )

    if not got_any_text:
        raise AgentInvocationError("L'agent n'a renvoyé aucune réponse exploitable.")


def _friendly_invoke_error(exc: ClientError) -> str:
    code = exc.response.get("Error", {}).get("Code", "")
    message = exc.response.get("Error", {}).get("Message", "")
    if code == "AccessDeniedException":
        return (
            "Accès refusé par IAM : le rôle assumé ne dispose pas de la "
            "permission bedrock-agentcore:InvokeHarness sur ce Harness."
        )
    if code == "ResourceNotFoundException":
        return (
            "AGENT_HARNESS_ARN introuvable : vérifiez l'ARN dans .env via "
            "`aws bedrock-agentcore-control list-harnesses`."
        )
    if code == "ThrottlingException":
        return "Le service est momentanément surchargé, réessayez."
    if code == "ValidationException":
        return f"Requête invalide envoyée à l'agent{f' : {message}' if message else '.'}"
    if code in ("ExpiredTokenException", "UnrecognizedClientException"):
        return "Session AWS expirée, reconnectez-vous."
    return f"Erreur lors de l'appel à l'agent ({code or 'inconnue'})."
