# -*- coding: utf-8 -*-
"""
Proxy routes — Anthropic & OpenAI compatible endpoints backed by kiro-cli.

Flow:
  Claude Code / Claude Desktop  →  these endpoints (client API key)
        →  pick a ksk_ account from the pool  →  kiro-cli subprocess
        →  clean text answer  →  wrapped back into Anthropic/OpenAI shape

This replaces the old HTTP-to-Kiro routing. The model field is accepted and
echoed back but the actual model is whatever the Kiro account serves.
"""

import json
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from loguru import logger

from kiro.account_pool import pick_account, report_success, report_failure, has_accounts
from kiro.kirocli_runner import run_chat, KiroCliError
from kiro.database import get_client_by_key, log_usage

router = APIRouter(tags=["proxy"])

# --- Auth: accept client API keys (from SQLite) on both header styles ---
_x_api_key = APIKeyHeader(name="x-api-key", auto_error=False)
_authorization = APIKeyHeader(name="Authorization", auto_error=False)


def _resolve_client(x_api_key: Optional[str], authorization: Optional[str]):
    raw = x_api_key or ""
    if not raw and authorization:
        raw = authorization.removeprefix("Bearer ").strip()
    if not raw:
        logger.warning(
            "401: no API key — x-api-key=%r, authorization=%r",
            (x_api_key[:12] + "…") if x_api_key else None,
            (authorization[:20] + "…") if authorization else None,
        )
        raise HTTPException(401, "Missing API key")
    client = get_client_by_key(raw)
    if not client:
        logger.warning(
            "401: invalid API key (prefix=%r len=%d)",
            raw[:10], len(raw)
        )
        raise HTTPException(401, "Invalid API key")
    return client


async def verify_client(
    x_api_key: Optional[str] = Security(_x_api_key),
    authorization: Optional[str] = Security(_authorization),
):
    return _resolve_client(x_api_key, authorization)


# ---------------------------------------------------------------------------
# Prompt flattening — turn a messages array into a single text prompt
# ---------------------------------------------------------------------------

def _content_to_text(content) -> str:
    """Anthropic/OpenAI content can be a string or a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "content" in block:  # tool_result etc.
                    parts.append(_content_to_text(block["content"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _flatten_messages(messages: List[dict], system: Optional[str] = None) -> str:
    lines = []
    if system:
        lines.append(f"System: {_content_to_text(system)}")
    for msg in messages:
        role = msg.get("role", "user")
        text = _content_to_text(msg.get("content", ""))
        prefix = {"user": "User", "assistant": "Assistant", "system": "System"}.get(role, role.title())
        lines.append(f"{prefix}: {text}")
    lines.append("Assistant:")
    return "\n\n".join(lines)


async def _run_with_failover(prompt: str, client: dict, model: str, endpoint: str) -> tuple[str, dict]:
    """Try accounts until one succeeds. Returns (answer, account)."""
    if not has_accounts():
        raise HTTPException(503, "No Kiro accounts configured. Add ksk_ keys in /admin.")

    tried = set()
    last_err = None
    # Try up to the number of enabled accounts
    for _ in range(20):
        account = pick_account(exclude_ids=tried)
        if account is None:
            break
        tried.add(account["id"])
        try:
            answer = await run_chat(account["api_key"], prompt)
            report_success(account["id"])
            try:
                log_usage(
                    client_id=client["id"], client_name=client["name"],
                    account_id=str(account["id"]), account_label=account["label"],
                    model=model, input_tokens=_estimate(prompt),
                    output_tokens=_estimate(answer), status="ok", endpoint=endpoint,
                )
            except Exception:
                pass
            return answer, account
        except KiroCliError as e:
            last_err = str(e)
            report_failure(account["id"])
            logger.warning(f"Account {account['label']} failed: {e}")
            continue

    raise HTTPException(503, f"All Kiro accounts failed. Last error: {last_err}")


def _estimate(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------

class AnthropicRequest(BaseModel):
    model: str = "claude-sonnet-4-5"
    messages: List[dict]
    system: Optional[object] = None
    max_tokens: int = 4096
    stream: bool = False
    temperature: Optional[float] = None


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    x_api_key: Optional[str] = Security(_x_api_key),
    authorization: Optional[str] = Security(_authorization),
):
    body = await request.json()
    client = _resolve_client(x_api_key, authorization)

    model = body.get("model", "claude-sonnet-4-5")
    messages = body.get("messages", [])
    system = body.get("system")
    stream = body.get("stream", False)

    prompt = _flatten_messages(messages, system)
    answer, account = await _run_with_failover(prompt, client, model, "/v1/messages")

    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    in_tok = _estimate(prompt)
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


def _anthropic_stream(msg_id: str, model: str, answer: str, in_tok: int, out_tok: int):
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
    # Chunk the answer so clients render progressively
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
# OpenAI Chat Completions API
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def openai_chat(
    request: Request,
    x_api_key: Optional[str] = Security(_x_api_key),
    authorization: Optional[str] = Security(_authorization),
):
    body = await request.json()
    client = _resolve_client(x_api_key, authorization)

    model = body.get("model", "claude-sonnet-4-5")
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    prompt = _flatten_messages(messages)
    answer, account = await _run_with_failover(prompt, client, model, "/v1/chat/completions")

    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    in_tok = _estimate(prompt)
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


def _openai_stream(cmpl_id: str, created: int, model: str, answer: str):
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
# Model list (static — clients just need *a* list)
# ---------------------------------------------------------------------------

_MODELS = [
    "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
    "claude-sonnet-4", "claude-opus-4",
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
