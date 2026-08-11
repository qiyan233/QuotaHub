"""Static encryption of stored credentials."""
import sqlite3
from pathlib import Path

from app import db
from app.secrets import decrypt_secret, encrypt_secret, load_or_create_key


def test_encrypt_decrypt_roundtrip():
    assert decrypt_secret(encrypt_secret("auth=topsecret")) == "auth=topsecret"
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""


def test_encrypted_blobs_are_distinct():
    a = encrypt_secret("auth=aaa")
    b = encrypt_secret("auth=aaa")
    assert a != b  # Fernet uses random IVs


def test_legacy_plaintext_fallback():
    # Values written before encryption was enabled are returned as-is.
    assert decrypt_secret("auth=legacy-plain") == "auth=legacy-plain"


def test_db_roundtrip_encrypts_at_rest(temp_data_dir):
    db.init_db()
    row = db.create_opencode_account(
        name="sec", workspace_id="Default", auth_cookie="auth=secret", api_key="sk-abc1234567890"
    )
    got = db.get_opencode_account(row.id)
    assert got.auth_cookie == "auth=secret"
    assert got.api_key == "sk-abc1234567890"
    assert got.encrypted is True

    conn = sqlite3.connect(Path(temp_data_dir) / "quotahub.db")
    raw = conn.execute(
        "SELECT auth_cookie, api_key, encrypted FROM opencode_accounts WHERE id = ?", (row.id,)
    ).fetchone()
    conn.close()
    assert raw[2] == 1
    assert raw[0].startswith("gAAAAA")  # Fernet token prefix
    assert raw[1].startswith("gAAAAA")
    assert raw[0] != "auth=secret"


def test_db_roundtrip_ollama_encrypts_at_rest(temp_data_dir):
    db.init_db()
    row = db.create_ollama_account(name="sec", session_cookie="__Secure-session=xyz")
    got = db.get_ollama_account(row.id)
    assert got.session_cookie == "__Secure-session=xyz"
    assert got.encrypted is True

    conn = sqlite3.connect(Path(temp_data_dir) / "quotahub.db")
    raw = conn.execute(
        "SELECT session_cookie, encrypted FROM ollama_accounts WHERE id = ?", (row.id,)
    ).fetchone()
    conn.close()
    assert raw[1] == 1
    assert raw[0].startswith("gAAAAA")


def test_update_reencrypts(temp_data_dir):
    db.init_db()
    row = db.create_opencode_account(name="u", workspace_id="Default", auth_cookie="auth=old")
    db.update_opencode_account(row.id, auth_cookie="auth=new")
    assert db.get_opencode_account(row.id).auth_cookie == "auth=new"


def test_key_file_persists(temp_data_dir, monkeypatch):
    monkeypatch.delenv("QUOTAHUB_SECRET_KEY", raising=False)
    key1 = load_or_create_key()
    key2 = load_or_create_key()
    assert key1 == key2
    assert (Path(temp_data_dir) / "secret.key").exists()
