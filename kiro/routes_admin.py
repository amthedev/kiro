# -*- coding: utf-8 -*-
"""
Admin panel API routes — /admin/*

Password is stored as SHA-256 hash in SQLite (settings table).
On first access, /admin/api/setup allows setting the initial password.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from kiro.admin_ui import ADMIN_HTML
from kiro.database import (
    is_first_setup, set_admin_password, verify_admin_password,
    list_kiro_accounts, add_kiro_account, update_kiro_account,
    delete_kiro_account, toggle_kiro_account, set_kiro_account_email,
    set_kiro_account_profile_arn,
    list_api_clients, add_api_client, delete_api_client, toggle_api_client,
    get_usage_summary, get_recent_logs,
)
from kiro.kiro_client import verify_ksk_key, fetch_profile_arn

router = APIRouter(prefix="/admin", tags=["admin"])

_auth_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_admin(authorization: Optional[str] = Security(_auth_header)) -> bool:
    if is_first_setup():
        raise HTTPException(status_code=403, detail="Setup required")
    token = ""
    if authorization:
        token = authorization.removeprefix("Bearer ").strip()
    if not verify_admin_password(token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True


# ---------------------------------------------------------------------------
# HTML panel
# ---------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def admin_panel():
    return HTMLResponse(content=ADMIN_HTML)


# ---------------------------------------------------------------------------
# Setup (first access only)
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    password: str
    confirm: str


@router.get("/api/setup-status")
async def setup_status():
    return {"needs_setup": is_first_setup()}


@router.post("/api/setup")
async def admin_setup(body: SetupRequest):
    if not is_first_setup():
        raise HTTPException(400, "Already configured")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if body.password != body.confirm:
        raise HTTPException(400, "Passwords do not match")
    set_admin_password(body.password)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


@router.post("/api/login")
async def admin_login(body: LoginRequest):
    if is_first_setup():
        return {"ok": False, "needs_setup": True}
    return {"ok": verify_admin_password(body.password)}


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------

class ChangePasswordRequest(BaseModel):
    current: str
    new_password: str
    confirm: str


@router.post("/api/change-password")
async def change_password(body: ChangePasswordRequest, _: bool = Depends(verify_admin)):
    if not verify_admin_password(body.current):
        raise HTTPException(400, "Current password is incorrect")
    if len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if body.new_password != body.confirm:
        raise HTTPException(400, "Passwords do not match")
    set_admin_password(body.new_password)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/api/stats")
async def admin_stats(_: bool = Depends(verify_admin)):
    return get_usage_summary()


# ---------------------------------------------------------------------------
# Kiro accounts
# ---------------------------------------------------------------------------

class KiroAccountCreate(BaseModel):
    label: str
    api_key: str


class KiroAccountUpdate(KiroAccountCreate):
    enabled: bool = True


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/api/kiro-accounts")
async def kiro_accounts_list(_: bool = Depends(verify_admin)):
    accounts = list_kiro_accounts()
    for a in accounts:
        # Mask the ksk_ key, keep prefix visible
        key = a.get("api_key", "")
        a["api_key"] = (key[:10] + "…" + key[-4:]) if len(key) > 14 else "•••"
    return accounts


@router.get("/api/kirocli-status")
async def kirocli_status(_: bool = Depends(verify_admin)):
    """Compatibilidade: sempre retorna available=True (usamos HTTP direto)."""
    return {"available": True}


@router.post("/api/kiro-accounts")
async def kiro_account_create(body: KiroAccountCreate, _: bool = Depends(verify_admin)):
    if not body.label or not body.api_key:
        raise HTTPException(400, "label and api_key are required")
    if not body.api_key.startswith("ksk_"):
        raise HTTPException(400, "API key must start with 'ksk_'")
    # Valida a key e busca o profileArn
    profile_arn = await fetch_profile_arn(body.api_key)
    valid = await verify_ksk_key(body.api_key)
    new_id = add_kiro_account(body.label, body.api_key, profile_arn or valid)
    if profile_arn:
        set_kiro_account_profile_arn(new_id, profile_arn)
    return {"id": new_id, "label": body.label, "email": profile_arn,
            "verified": valid is not None}


@router.post("/api/kiro-accounts/{account_id}/verify")
async def kiro_account_verify(account_id: int, _: bool = Depends(verify_admin)):
    """Re-verifica uma chave armazenada via HTTP."""
    from kiro.database import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT api_key FROM kiro_accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Account not found")
    profile_arn = await fetch_profile_arn(row["api_key"])
    valid = await verify_ksk_key(row["api_key"])
    if profile_arn:
        set_kiro_account_profile_arn(account_id, profile_arn)
        set_kiro_account_email(account_id, profile_arn)
    return {"verified": valid is not None, "email": profile_arn}


@router.delete("/api/kiro-accounts/{account_id}")
async def kiro_account_delete(account_id: int, _: bool = Depends(verify_admin)):
    ok = delete_kiro_account(account_id)
    if not ok:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


@router.post("/api/kiro-accounts/{account_id}/toggle")
async def kiro_account_toggle(account_id: int, body: ToggleRequest, _: bool = Depends(verify_admin)):
    ok = toggle_kiro_account(account_id, body.enabled)
    if not ok:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# API clients
# ---------------------------------------------------------------------------

class ClientCreate(BaseModel):
    name: str
    note: str = ""


@router.get("/api/clients")
async def clients_list(_: bool = Depends(verify_admin)):
    return list_api_clients()


@router.post("/api/clients")
async def client_create(body: ClientCreate, _: bool = Depends(verify_admin)):
    if not body.name:
        raise HTTPException(400, "name is required")
    return add_api_client(body.name, body.note)


@router.delete("/api/clients/{client_id}")
async def client_delete(client_id: int, _: bool = Depends(verify_admin)):
    ok = delete_api_client(client_id)
    if not ok:
        raise HTTPException(404, "Client not found")
    return {"ok": True}


@router.post("/api/clients/{client_id}/toggle")
async def client_toggle(client_id: int, body: ToggleRequest, _: bool = Depends(verify_admin)):
    ok = toggle_api_client(client_id, body.enabled)
    if not ok:
        raise HTTPException(404, "Client not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Usage logs
# ---------------------------------------------------------------------------

@router.get("/api/logs")
async def usage_logs(_: bool = Depends(verify_admin)):
    return get_recent_logs(limit=200)


# ---------------------------------------------------------------------------
# Debug: raw response from Kiro API for an account
# ---------------------------------------------------------------------------

@router.get("/api/debug/account/{account_id}")
async def debug_account(account_id: int, _: bool = Depends(verify_admin)):
    """Mostra o response raw de ListAvailableModels para diagnosticar profileArn."""
    import httpx as _httpx
    from kiro.database import get_conn as _get_conn
    from kiro.kiro_client import _build_headers, KIRO_MODELS_URL

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT api_key, profile_arn FROM kiro_accounts WHERE id=?", (account_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Account not found")

    headers = _build_headers(row["api_key"])
    list_headers = dict(headers)
    list_headers["x-amz-target"] = "AmazonQDeveloperStreamingService.ListAvailableModels"
    list_headers.pop("x-amzn-kiro-agent-mode", None)
    list_headers.pop("x-amzn-codewhisperer-optout", None)

    async with _httpx.AsyncClient() as client:
        try:
            resp = await client.get(KIRO_MODELS_URL, headers=list_headers, timeout=15)
            return {
                "status": resp.status_code,
                "stored_profile_arn": row["profile_arn"],
                "response_keys": list(resp.json().keys()) if resp.status_code == 200 else None,
                "response_preview": resp.text[:500],
            }
        except Exception as e:
            return {"error": str(e)}
