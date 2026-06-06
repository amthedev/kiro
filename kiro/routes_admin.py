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
    delete_kiro_account, toggle_kiro_account,
    list_api_clients, add_api_client, delete_api_client, toggle_api_client,
    get_usage_summary, get_recent_logs,
)

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
    refresh_token: str
    profile_arn: Optional[str] = None
    region: str = "us-east-1"


class KiroAccountUpdate(KiroAccountCreate):
    enabled: bool = True


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/api/kiro-accounts")
async def kiro_accounts_list(_: bool = Depends(verify_admin)):
    accounts = list_kiro_accounts()
    for a in accounts:
        a["refresh_token"] = a["refresh_token"][:8] + "…" if a.get("refresh_token") else ""
    return accounts


@router.post("/api/kiro-accounts")
async def kiro_account_create(body: KiroAccountCreate, _: bool = Depends(verify_admin)):
    if not body.label or not body.refresh_token:
        raise HTTPException(400, "label and refresh_token are required")
    new_id = add_kiro_account(body.label, body.refresh_token, body.profile_arn, body.region)
    return {"id": new_id, "label": body.label}


@router.put("/api/kiro-accounts/{account_id}")
async def kiro_account_update(account_id: int, body: KiroAccountUpdate, _: bool = Depends(verify_admin)):
    ok = update_kiro_account(account_id, body.label, body.refresh_token, body.profile_arn, body.region, body.enabled)
    if not ok:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


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
