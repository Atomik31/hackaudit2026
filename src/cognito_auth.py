"""Authentification humaine contre un Amazon Cognito User Pool (SRP + MFA).

Étape 1 et 2 du flux décrit : l'utilisateur s'authentifie avec son mot de passe
(jamais transmis en clair grâce au protocole SRP) puis, si le pool l'exige,
complète un challenge MFA (TOTP ou SMS). En sortie, on récupère les jetons
JWT (ID token / access token / refresh token) qui serviront ensuite à
l'échange contre des identifiants AWS temporaires (voir aws_identity.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pycognito import Cognito
from pycognito.exceptions import (
    ForceChangePasswordException,
    SMSMFAChallengeException,
    SoftwareTokenMFAChallengeException,
)


class AuthStatus:
    SUCCESS = "SUCCESS"
    MFA_SOFTWARE_TOKEN = "MFA_SOFTWARE_TOKEN"
    MFA_SMS = "MFA_SMS"
    NEW_PASSWORD_REQUIRED = "NEW_PASSWORD_REQUIRED"
    ERROR = "ERROR"


@dataclass
class LoginOutcome:
    status: str
    cognito_user: Cognito | None = None
    mfa_tokens: dict[str, Any] | None = None
    error_message: str | None = None


def start_login(
    user_pool_id: str,
    app_client_id: str,
    region: str,
    app_client_secret: str | None,
    username: str,
    password: str,
) -> LoginOutcome:
    """Démarre l'authentification SRP. Ne renvoie jamais le mot de passe en clair."""
    cognito_user = Cognito(
        user_pool_id,
        app_client_id,
        user_pool_region=region,
        username=username,
        client_secret=app_client_secret or None,
    )
    try:
        cognito_user.authenticate(password=password)
        return LoginOutcome(status=AuthStatus.SUCCESS, cognito_user=cognito_user)
    except SoftwareTokenMFAChallengeException as exc:
        return LoginOutcome(
            status=AuthStatus.MFA_SOFTWARE_TOKEN,
            cognito_user=cognito_user,
            mfa_tokens=exc.get_tokens(),
        )
    except SMSMFAChallengeException as exc:
        return LoginOutcome(
            status=AuthStatus.MFA_SMS,
            cognito_user=cognito_user,
            mfa_tokens=exc.get_tokens(),
        )
    except ForceChangePasswordException:
        return LoginOutcome(
            status=AuthStatus.NEW_PASSWORD_REQUIRED,
            error_message=(
                "Ce compte utilise un mot de passe temporaire et doit être "
                "provisionné avant la première connexion (définition du mot de "
                "passe permanent + enrôlement MFA). Contactez un administrateur "
                "ou utilisez le portail d'onboarding dédié."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - on ne veut jamais fuiter la stack au navigateur
        return LoginOutcome(status=AuthStatus.ERROR, error_message=_friendly_auth_error(exc))


def submit_mfa_code(
    cognito_user: Cognito,
    status: str,
    mfa_tokens: dict[str, Any],
    code: str,
) -> LoginOutcome:
    try:
        if status == AuthStatus.MFA_SMS:
            cognito_user.respond_to_sms_mfa_challenge(code, mfa_tokens)
        else:
            cognito_user.respond_to_software_token_mfa_challenge(code, mfa_tokens)
        return LoginOutcome(status=AuthStatus.SUCCESS, cognito_user=cognito_user)
    except Exception as exc:  # noqa: BLE001
        return LoginOutcome(status=AuthStatus.ERROR, error_message=_friendly_auth_error(exc))


def ensure_fresh_tokens(cognito_user: Cognito) -> None:
    """Renouvelle access/ID token via le refresh token s'ils approchent l'expiration."""
    cognito_user.check_token()


def _friendly_auth_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "notauthorized" in lowered or "incorrect username or password" in lowered:
        return "Identifiants incorrects."
    if "usernotfound" in lowered:
        return "Identifiants incorrects."
    if "code mismatch" in lowered or "codemismatch" in lowered:
        return "Code MFA invalide."
    if "expired" in lowered:
        return "Session de connexion expirée, veuillez recommencer."
    if "too many requests" in lowered or "limitexceeded" in lowered:
        return "Trop de tentatives, réessayez dans quelques minutes."
    return "Échec de l'authentification."
