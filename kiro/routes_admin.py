# -*- coding: utf-8 -*-
"""
Admin panel API routes — /admin/*

All endpoints require the ADMIN_PASSWORD env var as Bearer token.
The HTML panel is served at GET /admin.
"""

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from kiro.admin_ui import ADMIN_HTML
from kiro.database import (
    list_kiro_accounts, add_kiro_account, update_kiro_account,
    delete_kiro_account, toggle_kiro_account,
    list_api_clients, add_api_client, delete_api_client, toggle_api_client,
    get_usage_summary, get_recent_logs,
)

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin-change-me")

_auth_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_admin(authorization: Optional[str] = Security(_auth_header)) -> bool:
    token = ""
    if authorization:
        if authorization.lower().startswith("bearer "):
            token = authorization[7:]
        else:
            token = authorization
    if token != ADMIN_PASSWORD:
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
# Login
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


@router.post("/api/login")
async def admin_login(body: LoginRequest):
    return {"ok": body.password == ADMIN_PASSWORD}


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
    # Never expose refresh tokens in list responses
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
