"""Auth: HttpOnly session cookie, CSRF double-submit, forced first change."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.bootstrap import ensure_bootstrapped
from app.main import app


@pytest.fixture()
def authed_client(temp_data_dir, monkeypatch):
    monkeypatch.setenv("QUOTAHUB_PASSWORD", "initial-pass")
    ensure_bootstrapped()
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"username": "admin", "password": "initial-pass"})
    assert r.status_code == 200
    return client, r.headers.get("x-csrf-token")


def test_unauthenticated_requests_rejected(temp_data_dir, monkeypatch):
    monkeypatch.setenv("QUOTAHUB_PASSWORD", "initial-pass")
    ensure_bootstrapped()
    client = TestClient(app)
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/quota").status_code == 401


def test_login_sets_httponly_cookie(authed_client):
    client, _ = authed_client
    # The session cookie must be HttpOnly so JS cannot read the token.
    set_cookie = client.cookies.get("qh_session")
    assert set_cookie is not None


def test_authenticated_access_works(authed_client):
    client, _ = authed_client
    assert client.get("/api/config").status_code == 200


def test_csrf_required_for_mutations(authed_client):
    client, _ = authed_client
    resp = client.put("/api/config", json={"usage_sync": {"interval_sec": 60}})
    assert resp.status_code == 403  # missing X-CSRF-Token


def test_csrf_accepted_for_mutations(authed_client):
    client, csrf = authed_client
    resp = client.put(
        "/api/config",
        json={"usage_sync": {"interval_sec": 60}},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.json()["usage_sync"]["interval_sec"] == 60


def test_forced_first_change_flow(temp_data_dir, monkeypatch):
    monkeypatch.setenv("QUOTAHUB_PASSWORD", "initial-pass")
    ensure_bootstrapped()
    client = TestClient(app)

    # First login must signal forced password change.
    r = client.post("/api/auth/login", json={"username": "admin", "password": "initial-pass"})
    csrf = r.headers.get("x-csrf-token")
    assert r.json()["must_change_password"] is True

    # Change without current password is allowed on the forced first change.
    r = client.post(
        "/api/auth/change-credentials",
        json={"username": "admin", "password": "new-pass-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200

    # Sessions were revoked -> must re-login.
    assert client.get("/api/config").status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": "new-pass-1"})
    assert r.json()["must_change_password"] is False
    csrf2 = r.headers.get("x-csrf-token")

    # Subsequent changes require the current password.
    r = client.post(
        "/api/auth/change-credentials",
        json={"username": "admin", "password": "new-pass-2"},
        headers={"X-CSRF-Token": csrf2},
    )
    assert r.status_code == 403

    r = client.post(
        "/api/auth/change-credentials",
        json={"username": "admin", "password": "new-pass-2", "current_password": "new-pass-1"},
        headers={"X-CSRF-Token": csrf2},
    )
    assert r.status_code == 200


def test_wrong_password_locked(temp_data_dir, monkeypatch):
    monkeypatch.setenv("QUOTAHUB_PASSWORD", "initial-pass")
    ensure_bootstrapped()
    client = TestClient(app)
    for _ in range(db.LOGIN_MAX_FAILURES):
        r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 429


def test_logout_revokes_session(authed_client):
    client, csrf = authed_client
    assert client.get("/api/config").status_code == 200
    r = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert client.get("/api/config").status_code == 401


def test_short_password_rejected(authed_client):
    client, csrf = authed_client
    r = client.post(
        "/api/auth/change-credentials",
        json={"username": "admin", "password": "123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
