from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import db
from .auth import (
    change_credentials,
    create_session_token,
    current_username,
    is_auth_enabled,
    is_secure_request,
    new_csrf_token,
    password_was_changed,
    require_auth,
    revoke_current_session,
    verify_credentials,
)
from .bootstrap import ensure_bootstrapped
from .analytics import build_overview
from .config import load_config, load_service_config, mask_cookie, mask_ollama_cookie, update_service_config
from .ollama_quota import fetch_all_ollama_quotas
from .opencode_key import fetch_api_key, mask_api_key
from .opencode_usage import resolve_account_workspace_id
from .quota import fetch_all_quotas, fetch_quota_for_account
from .referral import (
    apply_referral_reward,
    fetch_referral_summary,
    preview_referral_reward,
)
from .schemas import (
    OllamaAccountCreate,
    OllamaAccountUpdate,
    OpenCodeAccountCreate,
    OpenCodeAccountUpdate,
    ServiceConfigUpdate,
)
from .usage_sync import backfill_usage, sync_usage_incremental

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_sync_task: asyncio.Task[None] | None = None


async def restart_usage_sync_task() -> None:
    global _sync_task
    if _sync_task is not None:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
    service = load_service_config()
    if service.usage_sync.auto_sync:
        _sync_task = asyncio.create_task(_usage_auto_sync_loop())


async def _usage_auto_sync_loop() -> None:
    while True:
        service = load_service_config()
        settings = service.usage_sync
        if not settings.auto_sync:
            await asyncio.sleep(30)
            continue
        accounts = db.list_opencode_accounts(enabled_only=True)
        for account in accounts:
            try:
                await sync_usage_incremental(account)
            except Exception:
                pass
        await asyncio.sleep(settings.interval_sec)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _sync_task
    ensure_bootstrapped()
    service = load_service_config()
    if service.usage_sync.auto_sync:
        _sync_task = asyncio.create_task(_usage_auto_sync_loop())
    yield
    if _sync_task is not None:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="QuotaHub", version="0.2.0", lifespan=lifespan)

# CORS is scoped to local dev origins only. The production deployment serves the
# SPA from the same origin (FastAPI static fallback), so cross-origin access is
# not required — and wildcard origins would let any site issue credentialed
# requests. Vite dev server proxies /api on the same origin too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    """Add hardened security response headers on every response."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-XSS-Protection", "0")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self'",
    )
    if is_secure_request(request):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

accounts_router = APIRouter(prefix="/api/accounts", tags=["accounts"], dependencies=[Depends(require_auth)])


@app.post("/api/auth/login")
async def login(request: Request, body: dict[str, str]) -> dict:
    import time as _time

    username = (body.get("username") or "").strip() or db.DEFAULT_USERNAME
    password = (body.get("password") or "").strip()
    if not is_auth_enabled():
        return {"enabled": False}

    # Resolve real client IP (nginx sets X-Forwarded-For). Only the last
    # trusted hop is honored: if we are directly reachable (no proxy), take
    # request.client.host and ignore any X-Forwarded-For the client supplies.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and request.headers.get("x-forwarded-host"):
        ip = forwarded.split(",")[-1].strip() or request.client.host
    else:
        ip = request.client.host if request.client else "unknown"

    now = _time.time()
    locked, locked_until = db.is_login_locked(ip, now)
    if locked:
        remaining = int(locked_until - now)
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过多，请 {max(1, remaining // 60)} 分钟后重试",
            headers={"Retry-After": str(remaining)},
        )

    if not verify_credentials(username, password):
        fail_count = db.record_login_failure(ip, now)
        if fail_count >= db.LOGIN_MAX_FAILURES:
            raise HTTPException(
                status_code=429,
                detail=f"登录失败次数过多，已临时锁定。请稍后重试",
            )
        raise HTTPException(status_code=401, detail="账号或密码错误")

    db.reset_login_attempt(ip)

    secure = is_secure_request(request)
    session_token = create_session_token(username)
    csrf_token = new_csrf_token()

    response = JSONResponse(
        {
            "enabled": True,
            "username": username,
            "must_change_password": not password_was_changed(username),
        }
    )
    response.set_cookie(
        "qh_session",
        session_token,
        max_age=db.SESSION_TTL_SEC,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        "qh_csrf",
        csrf_token,
        max_age=db.SESSION_TTL_SEC,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )
    # The CSRF token is also returned in the body so the SPA can attach it to
    # mutating requests without reading the cookie via JS.
    response.headers["X-CSRF-Token"] = csrf_token
    return response


@app.post("/api/auth/logout", dependencies=[Depends(require_auth)])
async def logout(request: Request) -> dict:
    from .auth import current_session_hash

    token_hash = current_session_hash(request)
    if token_hash:
        revoke_current_session(token_hash)
    response = JSONResponse({"ok": True})
    response.delete_cookie("qh_session", path="/", secure=is_secure_request(request), samesite="lax", httponly=True)
    response.delete_cookie("qh_csrf", path="/", secure=is_secure_request(request), samesite="lax")
    return response


@app.get("/api/auth/session")
async def session_status(request: Request) -> dict:
    username = current_username(request)
    if not username:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "username": username,
        "must_change_password": not password_was_changed(username),
    }


@app.post("/api/auth/change-credentials", dependencies=[Depends(require_auth)])
async def change_credentials_endpoint(request: Request, body: dict[str, str]) -> dict:
    from .auth import current_session_hash, mark_password_changed

    current_password = (body.get("current_password") or "").strip()
    new_username = (body.get("username") or "").strip() or db.DEFAULT_USERNAME
    new_password = (body.get("password") or "").strip()
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少 6 位")

    username = current_username(request)
    # Require the current password, except on the forced first-login change
    # (the initial credential was set out-of-band by the operator).
    if password_was_changed(username or ""):
        if not current_password or not verify_credentials(username or "", current_password):
            raise HTTPException(status_code=403, detail="当前密码不正确")

    change_credentials(new_username, new_password)
    return {"ok": True, "username": new_username}


@app.get("/api/auth/status")
async def auth_status() -> dict:
    return {"enabled": is_auth_enabled()}


def _opencode_account_dict(row: db.OpenCodeAccountRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "workspace_id": row.workspace_id,
        "resolved_workspace_id": row.resolved_workspace_id,
        "auth_cookie_masked": mask_cookie(row.auth_cookie),
        "api_key_masked": mask_api_key(row.api_key) if row.api_key else "",
        "configured": bool(row.auth_cookie.strip()),
        "show_rolling": row.show_rolling,
        "show_weekly": row.show_weekly,
        "show_monthly": row.show_monthly,
        "enabled": row.enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _ollama_account_dict(row: db.OllamaAccountRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "session_cookie_masked": mask_ollama_cookie(row.session_cookie),
        "configured": bool(row.session_cookie.strip()),
        "show_session": row.show_session,
        "show_weekly": row.show_weekly,
        "enabled": row.enabled,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _build_config_response() -> dict[str, Any]:
    service = load_service_config()
    return {
        "refresh": {
            "ollama": {
                "auto_refresh": service.refresh_ollama.auto_refresh,
                "interval_sec": service.refresh_ollama.interval_sec,
            },
            "opencode_go": {
                "auto_refresh": service.refresh_opencode_go.auto_refresh,
                "interval_sec": service.refresh_opencode_go.interval_sec,
            },
        },
        "usage_sync": {
            "auto_sync": service.usage_sync.auto_sync,
            "interval_sec": service.usage_sync.interval_sec,
            "backfill_pages_per_request": service.usage_sync.backfill_pages_per_request,
            "max_pages_per_incremental": service.usage_sync.max_pages_per_incremental,
        },
        "accounts_imported": db.imported_flag_path().exists()
        or db.count_opencode_accounts() > 0
        or db.count_ollama_accounts() > 0,
        "opencode_accounts": [_opencode_account_dict(row) for row in db.list_opencode_accounts()],
        "ollama_accounts": [_ollama_account_dict(row) for row in db.list_ollama_accounts()],
    }


@accounts_router.get("/opencode")
async def list_opencode_accounts() -> list[dict[str, Any]]:
    return [_opencode_account_dict(row) for row in db.list_opencode_accounts()]


@accounts_router.post("/opencode")
async def create_opencode_account(body: OpenCodeAccountCreate) -> dict[str, Any]:
    if not body.auth_cookie.strip():
        raise HTTPException(status_code=400, detail="auth_cookie 不能为空")
    api_key_masked = ""
    workspace_id = body.workspace_id.strip() or "Default"
    try:
        resolved = await resolve_account_workspace_id(
            workspace_id, body.auth_cookie.strip(), None
        )
        key = await fetch_api_key(resolved, body.auth_cookie.strip())
        api_key_masked = mask_api_key(key)
    except Exception:
        api_key_masked = ""
    row = db.create_opencode_account(
        name=body.name.strip() or "OpenCode",
        workspace_id=workspace_id,
        auth_cookie=body.auth_cookie.strip(),
        api_key=api_key_masked,
        show_rolling=body.show_rolling,
        show_weekly=body.show_weekly,
        show_monthly=body.show_monthly,
        enabled=body.enabled,
    )
    if api_key_masked and row.resolved_workspace_id is None:
        db.update_opencode_account(row.id, resolved_workspace_id=resolved)
        row = db.get_opencode_account(row.id)
    return _opencode_account_dict(row)


@accounts_router.get("/opencode/{account_id}")
async def get_opencode_account(account_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _opencode_account_dict(row)


@accounts_router.put("/opencode/{account_id}")
async def update_opencode_account(account_id: str, body: OpenCodeAccountUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        fields["name"] = fields["name"].strip() or "OpenCode"
    if "workspace_id" in fields and fields["workspace_id"] is not None:
        fields["workspace_id"] = fields["workspace_id"].strip() or "Default"
        fields["resolved_workspace_id"] = None
    if "auth_cookie" in fields and fields["auth_cookie"] is not None:
        fields["auth_cookie"] = fields["auth_cookie"].strip()
        # Re-fetch API key (masked only) when auth cookie changes.
        try:
            current = db.get_opencode_account(account_id)
            ws = fields.get("resolved_workspace_id") or (
                current.resolved_workspace_id if current else None
            ) or fields.get("workspace_id") or (current.workspace_id if current else "Default")
            resolved = await resolve_account_workspace_id(ws, fields["auth_cookie"], None)
            key = await fetch_api_key(resolved, fields["auth_cookie"])
            fields["api_key"] = mask_api_key(key)
            fields["resolved_workspace_id"] = resolved
        except Exception:
            fields["api_key"] = ""
    row = db.update_opencode_account(account_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _opencode_account_dict(row)


@accounts_router.delete("/opencode/{account_id}")
async def delete_opencode_account(account_id: str) -> dict[str, bool]:
    if not db.delete_opencode_account(account_id):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"ok": True}


@accounts_router.post("/opencode/{account_id}/test")
async def test_opencode_account(account_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        workspace_id = await resolve_account_workspace_id(
            row.workspace_id,
            row.auth_cookie,
            row.resolved_workspace_id,
        )
        db.update_opencode_account(account_id, resolved_workspace_id=workspace_id)
        return {"success": True, "workspace_id": workspace_id}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@accounts_router.post("/opencode/{account_id}/key/refresh")
async def refresh_opencode_api_key(account_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        workspace_id = await resolve_account_workspace_id(
            row.workspace_id,
            row.auth_cookie,
            row.resolved_workspace_id,
        )
        key = await fetch_api_key(workspace_id, row.auth_cookie)
        masked = mask_api_key(key)
        db.update_opencode_account(
            account_id, api_key=masked, resolved_workspace_id=workspace_id
        )
        return {"success": True, "api_key_masked": masked}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@accounts_router.get("/opencode/{account_id}/quota")
async def opencode_account_quota(account_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    from .config import AccountConfig

    account = AccountConfig(
        name=row.name,
        workspace_id=row.workspace_id,
        auth_cookie=row.auth_cookie,
        show_rolling=row.show_rolling,
        show_weekly=row.show_weekly,
        show_monthly=row.show_monthly,
    )
    quota = await fetch_quota_for_account(account, 0)
    return quota.to_dict()

@accounts_router.get("/opencode/{account_id}/referral")
async def opencode_account_referral(account_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    workspace_id = row.resolved_workspace_id or row.workspace_id
    try:
        summary = await fetch_referral_summary(workspace_id, row.auth_cookie)
        return {"success": True, **summary.to_dict()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@accounts_router.post("/opencode/{account_id}/referral/{reward_id}/preview")
async def opencode_referral_preview(account_id: str, reward_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    workspace_id = row.resolved_workspace_id or row.workspace_id
    try:
        preview = await preview_referral_reward(workspace_id, row.auth_cookie, reward_id)
        return {"success": True, **preview.to_dict()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@accounts_router.post("/opencode/{account_id}/referral/{reward_id}/apply")
async def opencode_referral_apply(account_id: str, reward_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    workspace_id = row.resolved_workspace_id or row.workspace_id
    try:
        await apply_referral_reward(workspace_id, row.auth_cookie, reward_id)
        summary = await fetch_referral_summary(workspace_id, row.auth_cookie)
        return {"success": True, **summary.to_dict()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@accounts_router.get("/opencode/{account_id}/usage")
async def list_account_usage(
    account_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    key_id: str | None = None,
) -> dict[str, Any]:
    if db.get_opencode_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    records, total = db.list_usage_records(account_id, offset=offset, limit=limit, key_id=key_id)
    sync = db.get_usage_sync_state(account_id)
    return {
        "records": [r.to_dict() for r in records],
        "total": total,
        "offset": offset,
        "limit": limit,
        "key_ids": db.list_usage_key_ids(account_id),
        "sync": sync.to_dict(),
    }


@accounts_router.get("/opencode/{account_id}/usage/status")
async def usage_sync_status(account_id: str) -> dict[str, Any]:
    if db.get_opencode_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return db.get_usage_sync_state(account_id).to_dict()


@accounts_router.post("/opencode/{account_id}/usage/sync")
async def usage_sync(account_id: str) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        result = await sync_usage_incremental(row)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@accounts_router.post("/opencode/{account_id}/usage/backfill")
async def usage_backfill(
    account_id: str,
    pages: int = Query(5, ge=1, le=50),
) -> dict[str, Any]:
    row = db.get_opencode_account(account_id)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        result = await backfill_usage(row, max_pages=pages)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@accounts_router.get("/ollama")
async def list_ollama_accounts() -> list[dict[str, Any]]:
    return [_ollama_account_dict(row) for row in db.list_ollama_accounts()]


@accounts_router.post("/ollama")
async def create_ollama_account(body: OllamaAccountCreate) -> dict[str, Any]:
    if not body.session_cookie.strip():
        raise HTTPException(status_code=400, detail="session_cookie 不能为空")
    row = db.create_ollama_account(
        name=body.name.strip() or "Ollama",
        session_cookie=body.session_cookie.strip(),
        show_session=body.show_session,
        show_weekly=body.show_weekly,
        enabled=body.enabled,
    )
    return _ollama_account_dict(row)


@accounts_router.put("/ollama/{account_id}")
async def update_ollama_account(account_id: str, body: OllamaAccountUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        fields["name"] = fields["name"].strip() or "Ollama"
    if "session_cookie" in fields and fields["session_cookie"] is not None:
        fields["session_cookie"] = fields["session_cookie"].strip()
    row = db.update_ollama_account(account_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return _ollama_account_dict(row)


@accounts_router.delete("/ollama/{account_id}")
async def delete_ollama_account(account_id: str) -> dict[str, bool]:
    if not db.delete_ollama_account(account_id):
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"ok": True}


app.include_router(accounts_router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/quota", dependencies=[Depends(require_auth)])
async def quota() -> list[dict]:
    try:
        cfg = load_config()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    rows = db.list_opencode_accounts(enabled_only=True)
    if not rows:
        return []
    from .config import AccountConfig

    accounts = [
        AccountConfig(
            name=row.name,
            workspace_id=row.workspace_id,
            auth_cookie=row.auth_cookie,
            show_rolling=row.show_rolling,
            show_weekly=row.show_weekly,
            show_monthly=row.show_monthly,
        )
        for row in rows
    ]
    results = await fetch_all_quotas(accounts)
    id_by_name = {row.name: row.id for row in rows}
    for item in results:
        item["account_id"] = id_by_name.get(item.get("name", ""))
    return results


@app.get("/api/ollama/quota", dependencies=[Depends(require_auth)])
async def ollama_quota() -> list[dict]:
    try:
        cfg = load_config()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    rows = db.list_ollama_accounts(enabled_only=True)
    if not rows:
        return []
    from .config import OllamaAccountConfig

    accounts = [
        OllamaAccountConfig(
            name=row.name,
            session_cookie=row.session_cookie,
            show_session=row.show_session,
            show_weekly=row.show_weekly,
        )
        for row in rows
    ]
    results = await fetch_all_ollama_quotas(accounts)
    id_by_name = {row.name: row.id for row in rows}
    for item in results:
        item["account_id"] = id_by_name.get(item.get("name", ""))
    return results


@app.get("/api/analytics/overview", dependencies=[Depends(require_auth)])
async def analytics_overview() -> dict[str, Any]:
    try:
        return await build_overview()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/analytics/opencode/daily", dependencies=[Depends(require_auth)])
async def analytics_opencode_daily(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    return {"days": days, "stats": db.opencode_daily_stats(days)}


@app.get("/api/analytics/opencode/daily/models", dependencies=[Depends(require_auth)])
async def analytics_opencode_daily_models(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    return {"days": days, "stats": db.opencode_daily_model_stats(days)}


@app.get("/api/usage/all", dependencies=[Depends(require_auth)])
async def list_all_usage(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    account_id: str | None = None,
) -> dict[str, Any]:
    records, total = db.list_all_usage_records(offset=offset, limit=limit, account_id=account_id)
    accounts = db.list_opencode_accounts()
    return {
        "records": [r.to_dict() for r in records],
        "total": total,
        "offset": offset,
        "limit": limit,
        "accounts": [{"id": row.id, "name": row.name} for row in accounts],
    }


@app.get("/api/config", dependencies=[Depends(require_auth)])
async def config_status() -> dict:
    try:
        ensure_bootstrapped()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _build_config_response()


@app.put("/api/config", dependencies=[Depends(require_auth)])
async def update_config(body: ServiceConfigUpdate) -> dict:
    try:
        ensure_bootstrapped()
        updates: dict[str, Any] = {}
        if body.refresh is not None:
            updates["refresh"] = {
                key: value.model_dump(exclude_unset=True)
                for key, value in body.refresh.items()
            }
        if body.usage_sync is not None:
            updates["usage_sync"] = body.usage_sync.model_dump(exclude_unset=True)
        if body.opencode is not None:
            updates["opencode"] = body.opencode.model_dump(exclude_unset=True)
        update_service_config(updates)
        await restart_usage_sync_task()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _build_config_response()


if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        static_file = (FRONTEND_DIST / full_path).resolve()
        try:
            static_file.relative_to(FRONTEND_DIST.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404) from exc
        if static_file.is_file():
            return FileResponse(static_file)
        index = FRONTEND_DIST / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404)
        return FileResponse(index)


def run() -> None:
    import uvicorn

    cfg = load_service_config()
    uvicorn.run(
        "app.main:app",
        host=cfg.listen_host,
        port=cfg.listen_port,
        reload=False,
    )


if __name__ == "__main__":
    run()
