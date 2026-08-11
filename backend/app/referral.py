from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from .config import AccountConfig

OPENCODE_ORIGIN = "https://opencode.ai"
REFERRAL_PREVIEW_SERVER_ID = (
    "46625df0aecf05f270f7ae4612cde374d11350c8abaf8649027572228b8af150"
)
REFERRAL_APPLY_SERVER_ID = (
    "f386778c1b78eade3e6acff87c9284e02fcd86826463c080526143c4fe8fff23"
)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Gecko/20100101 Firefox/148.0"
TIMEOUT = 10.0
MAX_HTML_BYTES = 4 << 20


@dataclass
class ReferralReward:
    id: str
    source: str
    status: str
    email: str
    amount: float
    time_created: str | None
    time_applied: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "status": self.status,
            "email": self.email,
            "amount": self.amount,
            "time_created": self.time_created,
            "time_applied": self.time_applied,
        }


@dataclass
class ReferralSummary:
    referral_code: str
    has_referral: bool
    reward_amount: float
    rewards: list[ReferralReward] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "referral_code": self.referral_code,
            "has_referral": self.has_referral,
            "reward_amount": self.reward_amount,
            "rewards": [r.to_dict() for r in self.rewards],
        }


@dataclass
class ReferralUsageWindow:
    before_percent: float
    after_percent: float
    reset_in_sec: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "before_percent": self.before_percent,
            "after_percent": self.after_percent,
            "reset_in_sec": self.reset_in_sec,
        }


@dataclass
class ReferralUsagePreview:
    rolling_usage: ReferralUsageWindow
    weekly_usage: ReferralUsageWindow
    monthly_usage: ReferralUsageWindow

    def to_dict(self) -> dict[str, Any]:
        return {
            "rolling_usage": self.rolling_usage.to_dict(),
            "weekly_usage": self.weekly_usage.to_dict(),
            "monthly_usage": self.monthly_usage.to_dict(),
        }


def build_cookie_header(auth_cookie: str) -> str:
    cookie = auth_cookie.strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie[7:].strip()
    if not cookie:
        return ""
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("auth="):
            return part
    return f"auth={cookie}"


def require_auth_cookie(auth_cookie: str) -> str:
    if not auth_cookie.strip():
        raise ValueError("Auth Cookie 为空")
    return build_cookie_header(auth_cookie)


def _find_matching(text: str, start: int, open_ch: str, close_ch: str) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    index = start
    while index < len(text):
        byte = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif byte == "\\":
                escaped = True
            elif byte == quote:
                quote = None
            index += 1
            continue
        if byte in ('"', "'"):
            quote = byte
        elif byte == open_ch:
            depth += 1
        elif byte == close_ch:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _skip_whitespace_and_resource(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if text[index:].startswith("$R["):
        close_rel = text[index:].find("]")
        if close_rel >= 0:
            close = index + close_rel
            equals_rel = text[close:].find("=")
            if equals_rel >= 0 and equals_rel < 16:
                index = close + equals_rel + 1
                while index < len(text) and text[index].isspace():
                    index += 1
    return index


def _read_value_at(text: str, start: int) -> str | None:
    index = _skip_whitespace_and_resource(text, start)
    if index >= len(text):
        return None
    byte = text[index]
    if byte in "{([":
        close_ch = {"{": "}", "[": "]", "(": ")"}[byte]
        end = _find_matching(text, index, byte, close_ch)
        if end is not None:
            return text[index : end + 1]
        return None
    if byte in ('"', "'"):
        end = _find_matching(text, index, byte, byte)
        if end is not None:
            return text[index : end + 1]
        return None
    if text[index:].startswith("new Date"):
        open_idx = index + text[index:].find("(")
        close_idx = _find_matching(text, open_idx, "(", ")")
        if close_idx is not None:
            return text[index : close_idx + 1]
        return None
    end = index
    while end < len(text) and text[end] not in ",}]":
        end += 1
    return text[index:end].strip()


def _read_field_value(text: str, field: str) -> str | None:
    pattern = re.compile(
        rf'(?m)(?:^|[\s,{{[])(?:"{re.escape(field)}"|\'{re.escape(field)}\'|{re.escape(field)})\s*:'
    )
    match = pattern.search(text)
    if not match:
        return None
    return _read_value_at(text, match.end())


def _parse_js_string(raw: str) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            import json

            return json.loads(value)
        except Exception:
            return value.strip('"')
    if value.startswith("'"):
        return (
            value.strip("'")
            .replace("\\'", "'")
            .replace('\\"', '"')
            .replace("\\\\", "\\")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )
    return value


def _parse_nullable_string(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value or value in ("null", "undefined"):
        return None
    if value.startswith("new Date"):
        open_idx = value.find("(")
        close_idx = value.rfind(")")
        if open_idx >= 0 and close_idx > open_idx:
            return _parse_nullable_string(value[open_idx + 1 : close_idx])
    if value.startswith('"') or value.startswith("'"):
        return _parse_js_string(value)
    try:
        number = float(value)
    except ValueError:
        return value
    millis = int(number) if number > 10_000_000_000.0 else int(number * 1000.0)
    try:
        return datetime.fromtimestamp(millis / 1000.0, tz=UTC).isoformat().replace("+00:00", "Z")
    except Exception:
        return value


def _parse_string(raw: str | None) -> str:
    return _parse_nullable_string(raw) or ""


def _parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    value = raw.strip()
    if value.startswith('"') or value.startswith("'"):
        value = _parse_js_string(value)
    match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_bool(raw: str | None) -> bool:
    return (raw or "").strip() == "true"


def _find_referral_object(html: str) -> str | None:
    marker = html.find("referralCode")
    if marker < 0:
        return None
    search_end = marker
    while search_end > 0:
        start = html[:search_end].rfind("{")
        if start < 0:
            break
        end = _find_matching(html, start, "{", "}")
        if end is not None and end > marker:
            block = html[start : end + 1]
            if "rewardAmount" in block and "rewards" in block:
                return block
        search_end = start
    return None


def _parse_reward_object(block: str) -> ReferralReward:
    return ReferralReward(
        id=_parse_string(_read_field_value(block, "id")),
        source=_parse_string(_read_field_value(block, "source")),
        status=_parse_string(_read_field_value(block, "status")),
        email=_parse_string(_read_field_value(block, "email")),
        amount=round((_parse_number(_read_field_value(block, "amount")) or 0.0) / 100.0, 2),
        time_created=_parse_nullable_string(_read_field_value(block, "timeCreated")),
        time_applied=_parse_nullable_string(_read_field_value(block, "timeApplied")),
    )


def _parse_rewards(block: str) -> list[ReferralReward]:
    raw = _read_field_value(block, "rewards") or ""
    if not raw.strip().startswith("["):
        return []
    rewards: list[ReferralReward] = []
    index = 0
    while index < len(raw):
        if raw[index] == "{":
            end = _find_matching(raw, index, "{", "}")
            if end is not None:
                reward = _parse_reward_object(raw[index : end + 1])
                if reward.id:
                    rewards.append(reward)
                index = end
        index += 1
    return rewards


def parse_referral_summary(html: str) -> ReferralSummary:
    block = _find_referral_object(html)
    if block is None:
        raise ValueError("未解析到 OpenCode 邀请奖励数据，页面结构可能已变化")
    return ReferralSummary(
        referral_code=_parse_string(_read_field_value(block, "referralCode")),
        has_referral=_parse_bool(_read_field_value(block, "hasReferral")),
        reward_amount=round((_parse_number(_read_field_value(block, "rewardAmount")) or 0.0) / 100.0, 2),
        rewards=_parse_rewards(block),
    )


async def fetch_referral_summary(workspace_id: str, auth_cookie: str) -> ReferralSummary:
    url = f"{OPENCODE_ORIGIN}/workspace/{quote(workspace_id, safe='')}/go"
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        resp = await client.get(
            url,
            headers={
                "Cookie": require_auth_cookie(auth_cookie),
                "User-Agent": USER_AGENT,
                "Accept": "text/html, application/xhtml+xml",
            },
        )
        if resp.status_code in (401, 403):
            raise ValueError(f"认证失败 (HTTP {resp.status_code})，请检查 auth cookie")
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ValueError(f"OpenCode Go 页面返回 HTTP {resp.status_code}")
        return parse_referral_summary(resp.text[:MAX_HTML_BYTES])


def _build_referral_server_body(workspace_id: str, reward_id: str) -> str:
    import json

    return json.dumps(
        {
            "t": {"t": 9, "i": 0, "l": 2, "a": [{"t": 1, "s": workspace_id}, {"t": 1, "s": reward_id}], "o": 0},
            "f": 31,
            "m": [],
        }
    )


async def _call_referral_server_action(
    workspace_id: str, auth_cookie: str, reward_id: str, server_id: str
) -> str:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        resp = await client.post(
            f"{OPENCODE_ORIGIN}/_server",
            headers={
                "Cookie": require_auth_cookie(auth_cookie),
                "Content-Type": "application/json",
                "X-Server-Id": server_id,
                "X-Server-Instance": server_id,
                "Origin": OPENCODE_ORIGIN,
                "Referer": f"{OPENCODE_ORIGIN}/workspace/{quote(workspace_id, safe='')}/go",
                "User-Agent": USER_AGENT,
            },
            content=_build_referral_server_body(workspace_id, reward_id),
        )
        if resp.status_code < 200 or resp.status_code >= 300:
            raise ValueError(f"OpenCode 奖励接口返回 HTTP {resp.status_code}")
        return resp.text


def _parse_preview_window(text: str, field: str) -> ReferralUsageWindow:
    block = _read_field_value(text, field)
    if block is None or not block.strip().startswith("{"):
        raise ValueError("OpenCode 奖励预览响应结构已变化")
    before = _parse_number(_read_field_value(block, "beforePercent"))
    after = _parse_number(_read_field_value(block, "afterPercent"))
    reset = _parse_number(_read_field_value(block, "resetInSec"))
    if before is None or after is None or reset is None:
        raise ValueError("OpenCode 奖励预览响应结构已变化")
    return ReferralUsageWindow(
        before_percent=before,
        after_percent=after,
        reset_in_sec=int(reset),
    )


def parse_referral_usage_preview(text: str) -> ReferralUsagePreview:
    return ReferralUsagePreview(
        rolling_usage=_parse_preview_window(text, "rollingUsage"),
        weekly_usage=_parse_preview_window(text, "weeklyUsage"),
        monthly_usage=_parse_preview_window(text, "monthlyUsage"),
    )


async def preview_referral_reward(
    workspace_id: str, auth_cookie: str, reward_id: str
) -> ReferralUsagePreview:
    text = await _call_referral_server_action(
        workspace_id, auth_cookie, reward_id, REFERRAL_PREVIEW_SERVER_ID
    )
    return parse_referral_usage_preview(text)


async def apply_referral_reward(
    workspace_id: str, auth_cookie: str, reward_id: str
) -> None:
    await _call_referral_server_action(
        workspace_id, auth_cookie, reward_id, REFERRAL_APPLY_SERVER_ID
    )
