from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from .quota import build_cookie_header

OPENCODE_ORIGIN = "https://opencode.ai"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Gecko/20100101 Firefox/148.0"
TIMEOUT = 10.0
MAX_HTML_BYTES = 4 << 20

RE_API_KEY = re.compile(r"sk-[A-Za-z0-9_-]{16,}")


def mask_api_key(key: str) -> str:
    """Mask as sk-<first3>****<last4>."""
    value = key.strip()
    if not value:
        return ""
    # Keep "sk-" prefix, then first 3 chars of the rest, ****, last 4 chars.
    prefix = ""
    rest = value
    if value.lower().startswith("sk-"):
        prefix = "sk-"
        rest = value[3:]
    if len(rest) <= 7:
        return f"{prefix}{rest[:3]}****{rest[-4:]}" if len(rest) > 3 else f"{prefix}****"
    return f"{prefix}{rest[:3]}****{rest[-4:]}"


async def fetch_api_key(workspace_id: str, auth_cookie: str) -> str:
    """Fetch the OpenCode Go API key from the /keys page."""
    cookie_header = build_cookie_header(auth_cookie)
    if not cookie_header:
        raise ValueError("OpenCode Go auth cookie 为空")

    url = f"{OPENCODE_ORIGIN}/workspace/{quote(workspace_id, safe='')}/keys"
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        resp = await client.get(
            url,
            headers={
                "Cookie": cookie_header,
                "User-Agent": USER_AGENT,
                "Accept": "text/html, application/xhtml+xml",
            },
        )
        if resp.status_code in (401, 403):
            raise ValueError(f"认证失败 (HTTP {resp.status_code})，请检查 auth cookie")
        if resp.status_code == 404:
            raise ValueError("工作区不存在 (HTTP 404)，请确认 workspace_id")
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ValueError(f"Keys 页面返回 HTTP {resp.status_code}")

        html = resp.text[:MAX_HTML_BYTES]
        match = RE_API_KEY.search(html)
        if not match:
            raise ValueError("未在 Keys 页面找到 API key")
        return match.group(0)
