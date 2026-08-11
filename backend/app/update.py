"""Self-update support: resolve the running version and check GitHub Releases.

For Docker deployments the image version is injected at build time via the
``QUOTAHUB_VERSION`` env var (see Dockerfile). For source / uv runs we fall
back to the ``VERSION`` file in the project root, then ``pyproject.toml``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# Mirror of the repo owner — the release workflow pushes images/releases under
# the lowercased owner, and the GitHub API is case-insensitive anyway.
REPO = "qiyan233/QuotaHub"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TIMEOUT = 8.0

VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _version_from_env() -> str | None:
    value = os.environ.get("QUOTAHUB_VERSION", "").strip()
    return value.lstrip("v") if value else None


def _version_from_file() -> str | None:
    # VERSION file sits at the repo/package root (one level above backend/).
    root = Path(__file__).resolve().parents[2]
    version_file = root / "VERSION"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return value.lstrip("v")
    return None


def _version_from_pyproject() -> str | None:
    # pyproject.toml lives in the backend/ directory (one level above app/).
    backend_dir = Path(__file__).resolve().parents[1]
    pyproject = backend_dir / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("version ="):
                return line.split("=", 1)[1].strip().strip('"').strip("'").lstrip("v")
    except OSError:
        return None
    return None


def current_version() -> str | None:
    """Return the running version as a normalized ``x.y.z`` string or None."""
    for loader in (_version_from_env, _version_from_file, _version_from_pyproject):
        version = loader()
        if version and VERSION_RE.match(version):
            return version
    return None


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Parse ``x.y.z`` (optionally ``v``-prefixed) into a comparable tuple."""
    match = VERSION_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def compare_versions(a: str, b: str) -> int:
    """Return -1 if a<b, 0 if equal, 1 if a>b. Unparseable -> 0 (treated equal)."""
    pa, pb = parse_version(a), parse_version(b)
    if pa is None or pb is None:
        return 0
    return (pa > pb) - (pa < pb)


@dataclass
class UpdateStatus:
    current_version: str
    latest_version: str
    update_available: bool
    checking: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "is_latest": not self.update_available,
            "checking": self.checking,
        }


async def check_for_update() -> UpdateStatus:
    """Query GitHub Releases for the latest tag and compare with the running version.

    On any failure (network, rate limit, missing version) we report the current
    version with ``latest_version`` empty and ``update_available=False`` so the
    panel degrades gracefully.
    """
    current = current_version() or ""
    if not current:
        return UpdateStatus(current_version=current, latest_version="", update_available=False)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(RELEASES_URL)
            if resp.status_code < 200 or resp.status_code >= 300:
                return UpdateStatus(current_version=current, latest_version="", update_available=False)
            data = resp.json()
            tag = str(data.get("tag_name") or data.get("name") or "")
            latest = tag.lstrip("v")
    except Exception:
        return UpdateStatus(current_version=current, latest_version="", update_available=False)

    if not parse_version(latest):
        return UpdateStatus(current_version=current, latest_version="", update_available=False)

    return UpdateStatus(
        current_version=current,
        latest_version=latest,
        update_available=compare_versions(latest, current) > 0,
    )
