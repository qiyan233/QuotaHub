"""Update check: version resolution, parsing, comparison, GitHub API handling."""
import httpx
import pytest
from httpx import Response

from app import update as upd


@pytest.fixture(autouse=True)
def clear_version_env(monkeypatch):
    monkeypatch.delenv("QUOTAHUB_VERSION", raising=False)


def test_repo_points_to_upstream():
    # Releases are published on the upstream repo, not this fork.
    assert upd.REPO == "lvmiao233/QuotaHub"


def test_parse_version():
    assert upd.parse_version("0.2.0") == (0, 2, 0)
    assert upd.parse_version("v1.2.3") == (1, 2, 3)
    assert upd.parse_version("1.10.0-beta.1") == (1, 10, 0)
    assert upd.parse_version("junk") is None


def test_compare_versions():
    assert upd.compare_versions("0.2.0", "0.2.1") == -1
    assert upd.compare_versions("0.2.1", "0.2.0") == 1
    assert upd.compare_versions("1.0.0", "1.0.0") == 0


def test_current_version_from_env(monkeypatch):
    monkeypatch.setenv("QUOTAHUB_VERSION", "v9.9.9")
    assert upd.current_version() == "9.9.9"


def test_current_version_falls_back_to_pyproject():
    # No env, no VERSION file in repo root -> pyproject version is used.
    assert upd.current_version() is not None
    assert upd.VERSION_RE.match(upd.current_version())


def test_current_version_unknown_without_sources(monkeypatch, tmp_path):
    # Simulate running without any version source.
    from unittest.mock import patch

    monkeypatch.setenv("QUOTAHUB_VERSION", "")
    with patch("app.update.Path", lambda *a, **k: tmp_path / "nonexistent"):
        assert upd.current_version() is None


def test_check_for_update_new_version_available(monkeypatch):
    import asyncio

    monkeypatch.setenv("QUOTAHUB_VERSION", "0.1.0")

    async def fake_get(self, url, **kwargs):
        return Response(200, json={"tag_name": "v0.3.0"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    status = asyncio.run(upd.check_for_update())
    assert status is not None
    assert status.update_available is True
    assert status.latest_version == "0.3.0"


def test_check_for_update_up_to_date(monkeypatch):
    import asyncio

    monkeypatch.setenv("QUOTAHUB_VERSION", "0.3.0")

    async def fake_get(self, url, **kwargs):
        return Response(200, json={"tag_name": "v0.3.0"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    status = asyncio.run(upd.check_for_update())
    assert status is not None
    assert status.update_available is False
    assert status.to_dict()["is_latest"] is True


def test_check_for_update_network_error_degrades(monkeypatch):
    import asyncio

    monkeypatch.setenv("QUOTAHUB_VERSION", "0.3.0")

    async def fake_get(self, url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    status = asyncio.run(upd.check_for_update())
    assert status is not None
    assert status.update_available is False
    assert status.latest_version == ""


def test_check_for_update_missing_version(monkeypatch):
    import asyncio

    from unittest.mock import patch

    monkeypatch.setenv("QUOTAHUB_VERSION", "")

    async def fake_get(self, url, **kwargs):
        return Response(200, json={"tag_name": "v0.3.0"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    # No version resolvable from any source -> gracefully degrades.
    with patch("app.update.current_version", return_value=None):
        status = asyncio.run(upd.check_for_update())
    assert status is not None
    assert status.update_available is False
    assert status.current_version == ""
