from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import load_service_config

_bearer = HTTPBearer(auto_error=False)

# In-memory issued tokens -> expiry timestamp (ms). Restart clears sessions.
_tokens: dict[str, int] = {}

TOKEN_TTL_SEC = 24 * 60 * 60  # 24 hours


def panel_password() -> str:
    return load_service_config().panel_password


def is_auth_enabled() -> bool:
    return bool(panel_password())


def _sign(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def verify_password(candidate: str) -> bool:
    expected = panel_password()
    if not expected:
        return False
    return hmac.compare_digest(_sign(candidate), _sign(expected))


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
