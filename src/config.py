"""Chargement de la configuration (variables d'environnement ou st.secrets)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

_CLIENTS_FILE = Path(__file__).resolve().parent.parent / "clients.json"


def _get(key: str, default: str | None = None, required: bool = False) -> str | None:
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    try:
        if key in st.secrets and st.secrets[key] != "":
            return st.secrets[key]
    except Exception:
        pass
    if required and not default:
        raise RuntimeError(
            f"Configuration manquante : la variable '{key}' doit être définie "
            "dans l'environnement, un fichier .env ou .streamlit/secrets.toml."
        )
    return default


@dataclass(frozen=True)
class ClientResources:
    """Un espace de travail = un bucket de dépôt (une entreprise cliente)."""

    slug: str
    label: str
    bucket: str
    bucket_region: str
    extract_tool: str
    verify_tool: str


@dataclass(frozen=True)
class AppConfig:
    cognito_region: str
    resource_region: str
    user_pool_id: str
    app_client_id: str
    app_client_secret: str | None
    identity_pool_id: str
    harness_arn: str
    harness_qualifier: str | None
    gateway_url: str
    gateway_protocol_version: str
    workspaces: dict[str, ClientResources]
    # Groupe Cognito (suffixe après "client-") -> liste des slugs d'espaces de
    # travail auxquels ce groupe donne accès. Un client gérant plusieurs
    # entreprises appartient à un groupe qui liste plusieurs espaces ; le
    # rôle IAM associé à ce groupe doit, côté AWS, autoriser exactement ces
    # buckets (voir README).
    client_groups: dict[str, list[str]]


def _load_workspace_config() -> tuple[dict[str, ClientResources], dict[str, list[str]]]:
    with _CLIENTS_FILE.open(encoding="utf-8") as f:
        raw = json.load(f)
    workspaces = {
        slug: ClientResources(
            slug=slug,
            label=entry["label"],
            bucket=entry["bucket"],
            bucket_region=entry["bucket_region"],
            extract_tool=entry["extract_tool"],
            verify_tool=entry["verify_tool"],
        )
        for slug, entry in raw["workspaces"].items()
    }
    return workspaces, raw["client_groups"]


@st.cache_resource
def load_config() -> AppConfig:
    resource_region = _get("AWS_REGION", "us-east-1")
    workspaces, client_groups = _load_workspace_config()
    return AppConfig(
        cognito_region=_get("COGNITO_REGION", resource_region),
        resource_region=resource_region,
        user_pool_id=_get("COGNITO_USER_POOL_ID", required=True),
        app_client_id=_get("COGNITO_APP_CLIENT_ID", required=True),
        app_client_secret=_get("COGNITO_APP_CLIENT_SECRET"),
        identity_pool_id=_get("COGNITO_IDENTITY_POOL_ID", required=True),
        harness_arn=_get("AGENT_HARNESS_ARN", required=True),
        harness_qualifier=_get("AGENT_HARNESS_QUALIFIER"),
        gateway_url=_get("AGENT_GATEWAY_URL", required=True),
        gateway_protocol_version=_get("AGENT_GATEWAY_MCP_PROTOCOL_VERSION", "2025-03-26"),
        workspaces=workspaces,
        client_groups=client_groups,
    )
