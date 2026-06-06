# -*- coding: utf-8 -*-
"""
Proxy routes — Anthropic & OpenAI compatible endpoints backed by HTTP calls to Kiro.

Flow:
  Claude Code / Claude Desktop  →  these endpoints (client API key)
        →  pick a ksk_ account from the pool  →  HTTP POST to runtime.kiro.dev
        →  SSE stream  →  wrapped back into Anthropic/OpenAI format
"""

import json
import time
import uuid
from typing import AsyncIterator, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from loguru import logger

from kiro.account_pool import pick_account, report_success, report_failure, has_accounts
from kiro.kiro_client import (
    call_kiro_streaming,
    call_kiro_complete,
    KiroAPIError,
    KiroAuthError,
)
from kiro.database import get_client_by_key, log_usage

router = APIRouter(tags=["proxy"])

# Shared httpx client (connection pooling)
_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            http2=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=True,
        )
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

_x_api_key = APIKeyHeader(name="x-api-key", auto_error=False)
_authorization = APIKeyHeader(name="Authorization", auto_error=False)


def _resolve_client(x_api_key: Optional[str], authorization: Optional[str]):
    raw = x_api_key or ""
    if not raw and authorization:
        raw = authorization.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(401, "Missing API key")
    client = get_client_by_key(raw)
    if not client:
        raise HTTPException(401, "Invalid API key")
    return client


async def verify_client(
    x_api_key: Optional[str] = Security(_x_api_key),
    authorization: Optional[str] = Security(_authorization),
):
    return _resolve_client(x_api_key, authorization)


# ---------------------------------------------------------------------------
# Core request runner with failover
# ---------------------------------------------------------------------------

async def _run_with_failover_stream(
    messages: List[dict],
    system: Optional[str],
    model: str,
    client_info: dict,
    endpoint: str,
) -> tuple[AsyncIterator[str], dict]:
    """
    Tenta contas em round-robin até uma funcionar.
    Retorna (async_iterator_de_chunks, account_dict).
    """
    if not has_accounts():
        raise HTTPException(503, "No Kiro accounts configured. Add ksk_ keys in /admin.")

    http = get_http_client()
    tried: set = set()
    last_err = "unknown error"

    for _ in range(20):
        account = pick_account(exclude_ids=tried)
        if account is None:
            break
        tried.add(account["id"])

        try:
            # Coleta toda a resposta para verificar se funcionou antes de stremar
            # (necessário para o failover funcionar corretamente)
            text, in_tok, out_tok = await _collect_with_retry(
                account, messages, system, model, http
            )
            report_success(account["id"])
            _log_usage(client_info, account, model, in_tok, out_tok, endpoint)

            async def _text_iterator(t=text):
                yield t

            return _text_iterator(), account

        except (KiroAPIError, KiroAuthError) as e:
            last_err = str(e)
            report_failure(account["id"])
            logger.warning(f"Account {account['label']} failed: {e}")
            continue

    raise HTTPException(503, f"All Kiro accounts failed. Last error: {last_err}")


async def _collect_with_retry(
    account: dict,
    messages: List[dict],
    system: Optional[str],
    model: str,
    http: httpx.AsyncClient,
    max_retries: int = 2,
) -> tuple[str, int, int]:
    """Coleta a resposta. Se profile_arn não está no banco, busca automaticamente."""
    profile_arn = account.get("profile_arn") or None

    # Se não tem profileArn salvo, tenta buscar agora e salvar para próximas requests
    if not profile_arn:
        from kiro.kiro_client import fetch_profile_arn
        from kiro.database import set_kiro_account_profile_arn
        profile_arn = await fetch_profile_arn(account["api_key"])
        if profile_arn:
            try:
                set_kiro_account_profile_arn(account["id"], profile_arn)
                logger.info(f"Account {account['label']}: fetched and saved profileArn")
            except Exception:
                pass
        else:
            logger.warning(f"Account {account['label']}: could not fetch profileArn, trying without it")

    for attempt in range(max_retries):
        try:
            text, in_tok, out_tok = await call_kiro_complete(
                account_id=account["id"],
                ksk_key=account["api_key"],
                messages=messages,
                system=system,
                model=model,
                profile_arn=profile_arn,
                client=http,
            )
            return text, in_tok, out_tok
        except KiroAPIError as e:
            if e.status_code == 403 and attempt < max_retries - 1:
                logger.debug(f"Account {account['label']}: 403, retrying...")
                continue
            raise

    raise KiroAPIError("Max retries exceeded", status_code=503)


def _log_usage(client_info: dict, account: dict, model: str, in_tok: int, out_tok: int, endpoint: str):
    try:
        log_usage(
            client_id=client_info["id"],
            client_name=client_info["name"],
            account_id=str(account["id"]),
            account_label=account["label"],
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            status="ok",
            endpoint=endpoint,
        )
    except Exception:
        pass


def _estimate(text: str) -> int:
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Anthropic Messages API  (/v1/messages)
# ---------------------------------------------------------------------------

@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    x_api_key: Optional[str] = Security(_x_api_key),
    authorization: Optional[str] = Security(_authorization),
):
    body = await request.json()
    client_info = _resolve_client(x_api_key, authorization)

    model = body.get("model", "claude-sonnet-4-5")
    messages = body.get("messages", [])
    system = body.get("system")
    stream = body.get("stream", False)

    # system pode ser lista de content blocks
    if isinstance(system, list):
        system = " ".join(
            b.get("text", "") for b in system
            if isinstance(b, dict) and b.get("type") == "text"
        )

    text_iter, account = await _run_with_failover_stream(
        messages, system, model, client_info, "/v1/messages"
    )

    # Coleta o texto (já foi coletado no failover, o iterator tem exatamente 1 chunk)
    answer = ""
    async for chunk in text_iter:
        answer += chunk

    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    in_tok = _estimate(" ".join(str(m.get("content", "")) for m in messages))
    out_tok = _estimate(answer)

    if stream:
        return StreamingResponse(
            _anthropic_stream(msg_id, model, answer, in_tok, out_tok),
            media_type="text/event-stream",
        )

    return JSONResponse({
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": answer}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
    })


async def _anthropic_stream(msg_id: str, model: str, answer: str, in_tok: int, out_tok: int):
    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    yield sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": 0},
        },
    })
    yield sse("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"type": "text", "text": ""},
    })
    chunk_size = 60
    for i in range(0, len(answer), chunk_size):
        piece = answer[i:i + chunk_size]
        yield sse("content_block_delta", {
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": piece},
        })
    yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": out_tok},
    })
    yield sse("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------
# OpenAI Chat Completions API  (/v1/chat/completions)
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def openai_chat(
    request: Request,
    x_api_key: Optional[str] = Security(_x_api_key),
    authorization: Optional[str] = Security(_authorization),
):
    body = await request.json()
    client_info = _resolve_client(x_api_key, authorization)

    model = body.get("model", "claude-sonnet-4-5")
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    # Extrai system prompt do messages[] (papel "system")
    system = None
    user_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            system = msg.get("content", "")
        else:
            user_messages.append(msg)

    text_iter, account = await _run_with_failover_stream(
        user_messages, system, model, client_info, "/v1/chat/completions"
    )

    answer = ""
    async for chunk in text_iter:
        answer += chunk

    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    in_tok = _estimate(" ".join(str(m.get("content", "")) for m in messages))
    out_tok = _estimate(answer)

    if stream:
        return StreamingResponse(
            _openai_stream(cmpl_id, created, model, answer),
            media_type="text/event-stream",
        )

    return JSONResponse({
        "id": cmpl_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": answer},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
    })


async def _openai_stream(cmpl_id: str, created: int, model: str, answer: str):
    def chunk(delta: dict, finish=None) -> str:
        payload = {
            "id": cmpl_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload)}\n\n"

    yield chunk({"role": "assistant"})
    chunk_size = 60
    for i in range(0, len(answer), chunk_size):
        yield chunk({"content": answer[i:i + chunk_size]})
    yield chunk({}, finish="stop")
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Model list (static)
# ---------------------------------------------------------------------------

_MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-sonnet-4",
    "claude-opus-4",
    "auto",
]


@router.get("/v1/models")
async def list_models(_: dict = Security(verify_client)):
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": now, "owned_by": "kiro"}
            for m in _MODELS
        ],
    }


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "accounts": has_accounts()}
