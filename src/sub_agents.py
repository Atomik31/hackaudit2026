"""Orchestration déterministe des sous-agents Extraction et Vérification.

Contexte : laisser l'agent manager (LLM) décider lui-même quand appeler ces
outils s'est révélé peu fiable, en particulier dès qu'une tâche demande
plusieurs appels enchaînés (extraire deux documents puis les comparer) — le
modèle produit alors parfois du texte plausible sans jamais déclencher
d'appel d'outil réel. Ce module retire cette décision du LLM : l'application
appelle elle-même, de façon déterministe, chaque sous-agent via le Gateway
MCP (avec les identifiants AWS temporaires de la session), et ne sollicite
le modèle que pour la tâche où son raisonnement est réellement utile
(comparer/analyser des contenus déjà extraits).
"""

from __future__ import annotations

import json

import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from src.aws_identity import TemporaryAwsCredentials


class SubAgentError(Exception):
    pass


def _call_mcp_tool(
    credentials: TemporaryAwsCredentials,
    region: str,
    gateway_url: str,
    protocol_version: str,
    tool_name: str,
    arguments: dict,
) -> dict:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
    }
    request = AWSRequest(method="POST", url=gateway_url, data=body, headers=headers)
    SigV4Auth(
        # Construire un objet credentials compatible botocore à partir des
        # identifiants temporaires de la session (access key, secret, token).
        _BotocoreCredentials(credentials),
        "bedrock-agentcore",
        region,
    ).add_auth(request)
    prepared = request.prepare()

    response = requests.post(gateway_url, data=body, headers=dict(prepared.headers), timeout=30)
    if response.status_code != 200:
        raise SubAgentError(f"Le Gateway a répondu {response.status_code} : {response.text[:300]}")

    payload = response.json()
    if "error" in payload:
        raise SubAgentError(f"Erreur MCP : {payload['error'].get('message', payload['error'])}")

    result = payload.get("result", {})
    if result.get("isError"):
        raise SubAgentError(f"Le sous-agent a renvoyé une erreur : {result}")

    content_blocks = result.get("content", [])
    if not content_blocks:
        raise SubAgentError("Le sous-agent n'a renvoyé aucun contenu.")

    raw_text = content_blocks[0].get("text", "{}")
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"raw": raw_text}
    if isinstance(parsed, dict) and "error" in parsed:
        raise SubAgentError(parsed["error"])
    return parsed


class _BotocoreCredentials:
    """Petit adaptateur : botocore.auth.SigV4Auth attend un objet credentials
    avec les attributs access_key/secret_key/token."""

    def __init__(self, credentials: TemporaryAwsCredentials) -> None:
        self.access_key = credentials.access_key_id
        self.secret_key = credentials.secret_access_key
        self.token = credentials.session_token


def extract_document(
    credentials: TemporaryAwsCredentials,
    region: str,
    gateway_url: str,
    protocol_version: str,
    extract_tool: str,
    document_key: str,
) -> str:
    """Appelle directement le sous-agent Extraction pour un document donné."""
    result = _call_mcp_tool(credentials, region, gateway_url, protocol_version, extract_tool, {"document_key": document_key})
    return result.get("extracted_text", "")


def verify_content(
    credentials: TemporaryAwsCredentials,
    region: str,
    gateway_url: str,
    protocol_version: str,
    verify_tool: str,
    content: str,
) -> str:
    """Appelle directement le sous-agent Vérification sur un contenu donné."""
    result = _call_mcp_tool(credentials, region, gateway_url, protocol_version, verify_tool, {"content": content})
    return result.get("analysis", "")
