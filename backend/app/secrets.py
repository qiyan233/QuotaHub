"""Static secret encryption for credentials stored in the database.

Sensitive fields (OpenCode auth cookie, Ollama session cookie, API keys) are
encrypted at rest with Fernet before being written to SQLite. The encryption
key is:

1. QUOTAHUB_SECRET_KEY env var (preferred), or
2. a key file ``secret.key`` inside the data directory, auto-generated on
   first run with 0600 permissions.

A stable key is required across restarts so previously encrypted values remain
readable. Back up the key alongside the database.
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import data_dir

KEY_FILE_NAME = "secret.key"
_KEY_CACHE: bytes | None = None
_KEY_CACHE_SOURCE: str | None = None  # env value or key file path the cache came from


def _read_key_file(path: Path) -> bytes | None:
    try:
        raw = path.read_bytes().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        base64.urlsafe_b64decode(raw)
    except Exception:
        return None
    return raw


def _write_key_file(path: Path, key: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(key)
        path.chmod(0o600)
    except OSError:
        # Best effort; on some platforms chmod may fail (e.g. mounted volumes).
        pass


def load_or_create_key() -> bytes:
    global _KEY_CACHE, _KEY_CACHE_SOURCE

    env_key = os.environ.get("QUOTAHUB_SECRET_KEY", "").strip()
    if env_key:
        if _KEY_CACHE is not None and _KEY_CACHE_SOURCE == env_key:
            return _KEY_CACHE
        try:
            base64.urlsafe_b64decode(env_key)
        except Exception:
            raise ValueError(
                "QUOTAHUB_SECRET_KEY 必须是 base64url 编码的 32 字节密钥，"
                "可用 `python -c \"import secrets;print(secrets.token_urlsafe(32))\"` 生成"
            ) from None
        _KEY_CACHE = env_key.encode("ascii")
        _KEY_CACHE_SOURCE = env_key
        return _KEY_CACHE

    key_file = data_dir() / KEY_FILE_NAME
    source = str(key_file)
    if _KEY_CACHE is not None and _KEY_CACHE_SOURCE == source:
        return _KEY_CACHE

    existing = _read_key_file(key_file)
    if existing is not None:
        _KEY_CACHE = existing
        _KEY_CACHE_SOURCE = source
        return _KEY_CACHE

    new_key = Fernet.generate_key()
    _write_key_file(key_file, new_key)
    _KEY_CACHE = new_key
    _KEY_CACHE_SOURCE = source
    return _KEY_CACHE


def _fernet() -> Fernet:
    return Fernet(load_or_create_key())


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret. Returns the Fernet token (str) or empty for blank input."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored secret. Returns plaintext, or '' if input is blank.

    If the value is not valid Fernet ciphertext (e.g. a legacy plaintext value
    written before encryption was enabled), it is returned as-is so existing
    deployments keep working.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Legacy plaintext fallback: older versions stored cookies in clear.
        return ciphertext


def rotate_secret_key(old_key: bytes, new_key: bytes) -> None:
    """Re-encrypt all stored secrets with a new key. (Not yet wired to an endpoint.)"""
    raise NotImplementedError
