from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db
from .config import load_service_config

_bearer = HTTPBearer(auto_error=False)

# In-memory issued tokens -> expiry timestamp (ms). Restart clears sessions.
_tokens: dict[str, int] = {}

TOKEN_TTL_SEC = 24 * 60 * 60  # 24 hours
PBKDF2_ITERATIONS = 200_000


def initial_password() -> str:
    return load_service_config().panel_password


def is_auth_enabled() -> bool:
    # Auth is considered enabled if a default password is set (enables first login).
    return bool(initial_password())


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def _new_salt() -> str:
    return secrets.token_hex(16)


def ensure_default_user() -> None:
    """Create the default admin user on first boot (password from QUOTAHUB_PASSWORD)."""
    if db.list_panel_users():
        return
    password = initial_password()
    if not password:
        return
    salt = _new_salt()
    db.upsert_panel_user(db.DEFAULT_USERNAME, _hash_password(password, salt), salt)


def verify_credentials(username: str, password: str) -> bool:
    user = db.get_panel_user(username.strip())
    if user is None:
        # Constant-time-ish: still hash to reduce username enumeration timing.
        _hash_password(password, _new_salt())
        return False
    expected = _hash_password(password, user["salt"])
    return hmac.compare_digest(expected, user["password_hash"])


def change_credentials(new_username: str, new_password: str) -> None:
    # Replace all existing panel users with the single new credential.
    for username in db.list_panel_users():
        db.delete_panel_user(username)
    salt = _new_salt()
    db.upsert_panel_user(
        new_username.strip() or db.DEFAULT_USERNAME,
        _hash_password(new_password, salt),
        salt,
    )
    # Reset all issued tokens so a credential change forces re-login everywhere.
    _tokens.clear()


def create_token() -> str:
    token = secrets.token_urlsafe(32)
    _tokens[token] = int(time.time()) + TOKEN_TTL_SEC
    return token


def _cleanup() -> None:
    now = int(time.time())
    expired = [t for t, exp in _tokens.items() if exp <= now]
    for t in expired:
        _tokens.pop(t, None)


def _token_valid(token: str) -> bool:
    exp = _tokens.get(token)
    if exp is None:
        return False
    if int(time.time()) > exp:
        _tokens.pop(token, None)
        return False
    return True


def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    if not is_auth_enabled():
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _cleanup()
    if not _token_valid(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期或无效",
            headers={"WWW-Authenticate": "Bearer"},
        )
