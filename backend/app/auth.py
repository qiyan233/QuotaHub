from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db
from .config import load_service_config

# ======================================================================
# Session auth over HttpOnly cookies + CSRF double-submit.
#
# * Session token is stored server-side (SQLite `auth_sessions`), delivered
#   to the browser in an HttpOnly / Secure / SameSite=Lax cookie so JS never
#   sees it.
# * CSRF: on login we also set a non-HttpOnly cookie `qh_csrf`. State-changing
#   requests must echo that value in the `X-CSRF-Token` header. Because
#   SameSite=Lax blocks cross-site POSTs anyway, the header check is defense
#   in depth for older browsers and subresource contexts.
# * Tokens are hashed at rest (SHA-256), so a DB leak can't replay sessions.
# ======================================================================

_bearer = HTTPBearer(auto_error=False)

PBKDF2_ITERATIONS = 600_000

SESSION_COOKIE_NAME = "qh_session"
CSRF_COOKIE_NAME = "qh_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_COOKIE_MAX_AGE = db.SESSION_TTL_SEC

# Paths that mutate state and therefore require a valid CSRF token.
_CSRF_REQUIRED_PREFIXES = (
    "/api/auth/logout",
    "/api/auth/change-credentials",
    "/api/accounts/",
    "/api/config",
)
# Methods that mutate state; anything not listed is read-only.
_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def initial_password() -> str:
    return load_service_config().panel_password


def is_auth_enabled() -> bool:
    return bool(initial_password())


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()


def _new_salt() -> str:
    return secrets.token_hex(16)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_default_user() -> None:
    """Create the default admin user on first boot (password from QUOTAHUB_PASSWORD).

    The initial account is created with ``password_changed=False`` so the first
    login is forced through the change-password flow.
    """
    if db.list_panel_users():
        return
    password = initial_password()
    if not password:
        return
    salt = _new_salt()
    db.upsert_panel_user(
        db.DEFAULT_USERNAME,
        _hash_password(password, salt),
        salt,
        password_changed=False,
    )


def verify_credentials(username: str, password: str) -> bool:
    user = db.get_panel_user(username.strip())
    if user is None:
        # Constant-time-ish: still hash to reduce username enumeration timing.
        _hash_password(password, _new_salt())
        return False
    expected = _hash_password(password, user["salt"])
    return hmac.compare_digest(expected, user["password_hash"])


def password_was_changed(username: str) -> bool:
    user = db.get_panel_user(username.strip())
    return bool(user and user["password_changed"])


def mark_password_changed(username: str) -> None:
    db.set_panel_user_password_changed(username.strip())


def change_credentials(new_username: str, new_password: str) -> None:
    # Replace all existing panel users with the single new credential.
    old_usernames = list(db.list_panel_users())
    for username in old_usernames:
        db.delete_panel_user(username)
    salt = _new_salt()
    db.upsert_panel_user(
        new_username.strip() or db.DEFAULT_USERNAME,
        _hash_password(new_password, salt),
        salt,
        password_changed=True,
    )
    # Reset all sessions so a credential change forces re-login everywhere.
    for username in old_usernames:
        db.revoke_all_sessions(username)


def create_session_token(username: str) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(_hash_token(token), username)
    return token


def revoke_current_session(token_hash: str) -> None:
    db.revoke_session(token_hash)


# ----------------------------------------------------------------------
# Cookie/header helpers
# ----------------------------------------------------------------------

def _session_cookie_attrs(secure: bool) -> dict[str, str | int]:
    attrs: dict[str, str | int] = {
        "key": SESSION_COOKIE_NAME,
        "value": "",
        "path": "/",
        "max_age": SESSION_COOKIE_MAX_AGE,
        "httponly": True,
        "samesite": "lax",
    }
    if secure:
        attrs["secure"] = True
    return attrs


def is_secure_request(request: Request) -> bool:
    # Trust the proxy-declared scheme (nginx/cloudflare set X-Forwarded-Proto).
    forwarded = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded:
        return forwarded.split(",")[0].strip() == "https"
    return request.url.scheme.lower() == "https"


def get_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE_NAME)


def get_csrf_token(request: Request) -> str | None:
    return request.cookies.get(CSRF_COOKIE_NAME)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def validate_csrf(request: Request, csrf_header: str | None) -> bool:
    expected = get_csrf_token(request)
    if not expected or not csrf_header:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), csrf_header.encode("utf-8"))


def _requires_csrf(request: Request) -> bool:
    if request.method not in _MUTATING_METHODS:
        return False
    path = request.url.path
    return any(path.startswith(prefix) for prefix in _CSRF_REQUIRED_PREFIXES)


def require_auth(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    x_csrf_token: Annotated[str | None, Header()] = None,
) -> None:
    if not is_auth_enabled():
        return

    # Backward compatible: an explicit Authorization: Bearer header still works.
    token = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if token is None:
        token = get_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    db.purge_expired_sessions()
    session = db.get_valid_session(_hash_token(token), _now_iso())
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期或无效",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if _requires_csrf(request) and not validate_csrf(request, x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF 校验失败，请刷新页面后重试",
        )


def current_username(request: Request) -> str | None:
    token = get_session_token(request)
    if not token:
        return None
    session = db.get_valid_session(_hash_token(token), _now_iso())
    return session["username"] if session else None


def current_session_hash(request: Request) -> str | None:
    token = get_session_token(request)
    return _hash_token(token) if token else None


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
