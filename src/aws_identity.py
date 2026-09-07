"""Étape 4 du flux : échange du JWT Cognito contre des identifiants AWS
temporaires via un Cognito Identity Pool (sts:AssumeRoleWithWebIdentity en
coulisses). Aucune clé statique n'est jamais utilisée côté application.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import boto3


@dataclass
class TemporaryAwsCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str
    expiration: datetime
    identity_id: str

    def is_expiring_soon(self, buffer_seconds: int = 120) -> bool:
        now = datetime.now(timezone.utc)
        remaining = (self.expiration - now).total_seconds()
        return remaining <= buffer_seconds


def exchange_id_token_for_credentials(
    region: str,
    identity_pool_id: str,
    user_pool_id: str,
    id_token: str,
) -> TemporaryAwsCredentials:
    identity_client = boto3.client("cognito-identity", region_name=region)
    login_provider = f"cognito-idp.{region}.amazonaws.com/{user_pool_id}"
    logins = {login_provider: id_token}

    identity = identity_client.get_id(IdentityPoolId=identity_pool_id, Logins=logins)
    identity_id = identity["IdentityId"]

    creds_response = identity_client.get_credentials_for_identity(
        IdentityId=identity_id,
        Logins=logins,
    )
    creds = creds_response["Credentials"]

    return TemporaryAwsCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretKey"],
        session_token=creds["SessionToken"],
        expiration=creds["Expiration"],
        identity_id=identity_id,
    )


def whoami(credentials: TemporaryAwsCredentials, region: str) -> str | None:
    """Retourne l'ARN du rôle IAM effectivement assumé (utile pour vérifier le
    moindre privilège en interface, sans exposer d'identifiant statique)."""
    try:
        sts_client = boto3.client(
            "sts",
            region_name=region,
            aws_access_key_id=credentials.access_key_id,
            aws_secret_access_key=credentials.secret_access_key,
            aws_session_token=credentials.session_token,
        )
        return sts_client.get_caller_identity().get("Arn")
    except Exception:  # noqa: BLE001
        return None
