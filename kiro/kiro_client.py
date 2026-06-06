# -*- coding: utf-8 -*-
"""
Kiro HTTP client — chamadas HTTP diretas ao Kiro.

Fluxo correto por conta (ksk_):
  1. ksk_ key É o refreshToken (token de longa duração)
  2. ksk_ → POST /refreshToken → { accessToken, refreshToken, profileArn, expiresIn }
  3. accessToken (curto prazo, ~1h) → Bearer nas chamadas ao runtime.kiro.dev
  4. profileArn → obrigatório no payload de cada request ao modelo
  5. Quando accessToken expira → repete o passo 2 com o refreshToken atualizado
"""

import asyncio
import hashlib
import json
import socket
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

REGION = "us-east-1"

# ksk_ é o refreshToken — troca por accessToken + profileArn
TOKEN_REFRESH_URL = f"https://prod.{REGION}.auth.desktop.kiro.dev/refreshToken"

# Endpoint de geração de respostas
KIRO_API_URL = f"https://runtime.{REGION}.kiro.dev/generateAssistantResponse"

# Margem de renovação: renova quando faltam 5 minutos para expirar
REFRESH_THRESHOLD = 300
REQUEST_TIMEOUT = 300.0


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------

class KiroAuthError(Exception):
    pass


class KiroAPIError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Cache de tokens por conta (account_id → estado)
# ---------------------------------------------------------------------------

class _TokenState:
    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None  # pode ser atualizado pelo server
        self.profile_arn: Optional[str] = None
        self.expires_at: Optional[datetime] = None
        self.lock = asyncio.Lock()

    def is_valid(self) -> bool:
        if not self.access_token or not self.expires_at:
            return False
        remaining = (self.expires_at - datetime.now(timezone.utc)).total_seconds()
        return remaining > REFRESH_THRESHOLD


_states: Dict[int, _TokenState] = {}


def _get_state(account_id: int) -> _TokenState:
    if account_id not in _states:
        _states[account_id] = _TokenState()
    return _states[account_id]


# ---------------------------------------------------------------------------
# Fingerprint para User-Agent
# ---------------------------------------------------------------------------

def _fingerprint() -> str:
    return hashlib.sha256(
        f"{socket.gethostname()}-kiro-gateway".encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Troca do ksk_ (refreshToken) por accessToken + profileArn
# ---------------------------------------------------------------------------

async def _do_refresh(refresh_token: str, client: httpx.AsyncClient) -> _TokenState:
    """
    POST /refreshToken com { refreshToken: ksk_... }
    Retorna novo estado com accessToken, refreshToken (pode ser novo), profileArn, expiresAt.
    """
    payload = {"refreshToken": refresh_token}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"KiroIDE/1.0 gateway/{_fingerprint()}",
    }

    try:
        resp = await client.post(TOKEN_REFRESH_URL, json=payload, headers=headers, timeout=30)
    except Exception as e:
        raise KiroAuthError(f"refreshToken request failed: {e}")

    if resp.status_code != 200:
        raise KiroAuthError(
            f"refreshToken returned {resp.status_code}: {resp.text[:300]}"
        )

    try:
        data = resp.json()
    except Exception:
        raise KiroAuthError(f"refreshToken response is not JSON: {resp.text[:200]}")

    access_token = data.get("accessToken")
    if not access_token:
        raise KiroAuthError(f"refreshToken response has no accessToken: {data}")

    state = _TokenState()
    state.access_token = access_token
    # Server pode retornar novo refreshToken — usa ele se vier, senão mantém o antigo
    state.refresh_token = data.get("refreshToken") or refresh_token
    state.profile_arn = data.get("profileArn")
    expires_in = data.get("expiresIn", 3600)
    state.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)

    logger.debug(
        f"Token refreshed — profileArn={'set' if state.profile_arn else 'MISSING'}, "
        f"expires_in={expires_in}s"
    )
    return state


async def get_tokens(
    account_id: int,
    ksk_key: str,
    http: httpx.AsyncClient,
) -> Tuple[str, Optional[str]]:
    """
    Retorna (access_token, profile_arn) válidos para a conta.
    Renova automaticamente quando necessário.
    """
    state = _get_state(account_id)

    async with state.lock:
        if state.is_valid():
            return state.access_token, state.profile_arn

        # Usa refreshToken atualizado se disponível, senão usa o ksk_ original
        refresh_token = state.refresh_token or ksk_key
        new_state = await _do_refresh(refresh_token, http)

        state.access_token = new_state.access_token
        state.refresh_token = new_state.refresh_token
        state.profile_arn = new_state.profile_arn
        state.expires_at = new_state.expires_at

        return state.access_token, state.profile_arn


# ---------------------------------------------------------------------------
# Verificação de chave (para o painel admin)
# ---------------------------------------------------------------------------

async def verify_ksk_key(ksk_key: str) -> Optional[str]:
    """
    Verifica se uma ksk_ key é válida fazendo refreshToken.
    Retorna o profileArn (ou "valid") em caso de sucesso, None em caso de falha.
    """
    async with httpx.AsyncClient() as client:
        try:
            state = await _do_refresh(ksk_key, client)
            return state.profile_arn or "valid"
        except KiroAuthError as e:
            logger.warning(f"verify_ksk_key failed: {e}")
            return None


async def fetch_profile_arn(ksk_key: str) -> Optional[str]:
    """
    Busca o profileArn fazendo refreshToken com a ksk_ key.
    """
    async with httpx.AsyncClient() as client:
        try:
            state = await _do_refresh(ksk_key, client)
            return state.profile_arn
        except KiroAuthError as e:
            logger.warning(f"fetch_profile_arn failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Headers para o runtime.kiro.dev
# ---------------------------------------------------------------------------

def _api_headers(access_token: str) -> dict:
    fp = _fingerprint()
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/x-amz-json-1.0",
        "x-amz-target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
        "User-Agent": (
            f"aws-sdk-js/1.0.27 ua/2.1 os/linux#5.0 lang/js md/nodejs#22.0.0 "
            f"api/codewhispererstreaming#1.0.27 m/E KiroIDE-0.7.45-{fp}"
        ),
        "x-amz-user-agent": f"aws-sdk-js/1.0.27 KiroIDE-0.7.45-{fp}",
        "x-amzn-codewhisperer-optout": "true",
        "x-amzn-kiro-agent-mode": "vibe",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=3",
    }


# ---------------------------------------------------------------------------
# Construção do payload
# ---------------------------------------------------------------------------

def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "content" in block:
                    parts.append(_content_to_text(block["content"]))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _merge_alternating(messages: List[dict]) -> List[dict]:
    """Mescla mensagens consecutivas do mesmo role."""
    if not messages:
        return []
    result = []
    for msg in messages:
        if result and result[-1]["role"] == msg["role"]:
            sep = "\n\n" if result[-1]["text"] and msg["text"] else ""
            result[-1]["text"] += sep + msg["text"]
        else:
            result.append({"role": msg["role"], "text": msg["text"]})
    return result


def build_kiro_payload(
    messages: List[dict],
    system: Optional[str],
    model_id: str,
    profile_arn: Optional[str],
) -> dict:
    """Constrói o payload para POST /generateAssistantResponse."""
    normalized: List[dict] = []

    if system:
        normalized.append({"role": "user", "text": _content_to_text(system)})
        normalized.append({"role": "assistant", "text": "Understood."})

    for msg in messages:
        role = msg.get("role", "user")
        text = _content_to_text(msg.get("content", ""))
        if role == "system":
            normalized.append({"role": "user", "text": text})
        else:
            normalized.append({"role": role, "text": text})

    normalized = _merge_alternating(normalized)

    if not normalized:
        normalized = [{"role": "user", "text": "(empty)"}]

    # Garante que termina com mensagem de usuário
    if normalized[-1]["role"] != "user":
        normalized.append({"role": "user", "text": "(continue)"})

    history_msgs = normalized[:-1]
    current_text = normalized[-1]["text"] or "(empty)"

    history = []
    for msg in history_msgs:
        if msg["role"] == "user":
            history.append({
                "userInputMessage": {
                    "content": msg["text"] or "(empty placeholder)",
                    "modelId": model_id,
                    "origin": "AI_EDITOR",
                }
            })
        else:
            history.append({
                "assistantResponseMessage": {
                    "content": msg["text"] or "(empty placeholder)",
                }
            })

    payload: dict = {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "conversationId": str(uuid.uuid4()),
            "currentMessage": {
                "userInputMessage": {
                    "content": current_text,
                    "modelId": model_id,
                    "origin": "AI_EDITOR",
                }
            },
        }
    }

    if history:
        payload["conversationState"]["history"] = history

    if profile_arn:
        payload["profileArn"] = profile_arn

    return payload


# ---------------------------------------------------------------------------
# Mapeamento de modelos
# ---------------------------------------------------------------------------

_MODEL_MAP: Dict[str, str] = {
    "claude-opus-4-5":            "claude-opus-4.5",
    "claude-opus-4-5-20251101":   "claude-opus-4.5",
    "claude-haiku-4-5":           "claude-haiku-4.5",
    "claude-haiku-4.5":           "claude-haiku-4.5",
    "claude-sonnet-4-5":          "CLAUDE_SONNET_4_5_20250929_V1_0",
    "claude-sonnet-4-5-20250929": "CLAUDE_SONNET_4_5_20250929_V1_0",
    "claude-sonnet-4":            "CLAUDE_SONNET_4_20250514_V1_0",
    "claude-sonnet-4-20250514":   "CLAUDE_SONNET_4_20250514_V1_0",
    "claude-3-7-sonnet-20250219": "CLAUDE_3_7_SONNET_20250219_V1_0",
    "auto":                       "claude-sonnet-4.5",
}


def resolve_model_id(model_name: str) -> str:
    return _MODEL_MAP.get(model_name, model_name)


# ---------------------------------------------------------------------------
# Chamada ao Kiro com streaming
# ---------------------------------------------------------------------------

async def call_kiro_streaming(
    account_id: int,
    ksk_key: str,
    messages: List[dict],
    system: Optional[str],
    model: str,
    client: httpx.AsyncClient,
    profile_arn: Optional[str] = None,
) -> AsyncIterator[str]:
    """Envia request ao Kiro e retorna chunks de texto via async iterator."""
    access_token, fetched_arn = await get_tokens(account_id, ksk_key, client)

    # Usa profileArn do cache de tokens (mais confiável) ou o passado como parâmetro
    arn = fetched_arn or profile_arn
    if not arn:
        raise KiroAPIError("profileArn not available — token refresh may have failed", status_code=400)

    model_id = resolve_model_id(model)
    payload = build_kiro_payload(messages, system, model_id, arn)
    headers = _api_headers(access_token)

    async with client.stream(
        "POST",
        KIRO_API_URL,
        content=json.dumps(payload).encode(),
        headers=headers,
        timeout=httpx.Timeout(connect=30.0, read=REQUEST_TIMEOUT, write=30.0, pool=30.0),
    ) as resp:
        if resp.status_code == 403:
            # Invalida o cache para forçar novo refresh na próxima tentativa
            _get_state(account_id).access_token = None
            raise KiroAPIError("Access token rejected (403)", status_code=403)

        if resp.status_code != 200:
            body = await resp.aread()
            raise KiroAPIError(
                f"Kiro API returned {resp.status_code}: {body.decode(errors='replace')[:300]}",
                status_code=resp.status_code,
            )

        async for chunk in _parse_sse_stream(resp):
            yield chunk


async def call_kiro_complete(
    account_id: int,
    ksk_key: str,
    messages: List[dict],
    system: Optional[str],
    model: str,
    client: httpx.AsyncClient,
    profile_arn: Optional[str] = None,
) -> Tuple[str, int, int]:
    """Versão não-streaming: retorna (text, in_tokens, out_tokens)."""
    parts = []
    async for chunk in call_kiro_streaming(
        account_id, ksk_key, messages, system, model, client, profile_arn
    ):
        parts.append(chunk)

    text = "".join(parts)
    out_tokens = max(1, len(text) // 4)
    return text, 0, out_tokens


# ---------------------------------------------------------------------------
# Parser SSE
# ---------------------------------------------------------------------------

async def _parse_sse_stream(response: httpx.Response) -> AsyncIterator[str]:
    """Parseia o stream SSE do Kiro e extrai chunks de texto."""
    buffer = ""
    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("data:"):
            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue
            try:
                data = json.loads(data_str)
                buffer = ""
            except json.JSONDecodeError:
                buffer += data_str
                try:
                    data = json.loads(buffer)
                    buffer = ""
                except json.JSONDecodeError:
                    continue

            text = _extract_text(data)
            if text:
                yield text

        elif line.startswith("{"):
            buffer += line
            try:
                data = json.loads(buffer)
                buffer = ""
                text = _extract_text(data)
                if text:
                    yield text
            except json.JSONDecodeError:
                pass


def _extract_text(event: dict) -> str:
    """Extrai texto de um evento SSE do Kiro."""
    # {"output": {"message": {"content": [{"type": "text", "text": "..."}]}}}
    output = event.get("output", {})
    if output:
        content = output.get("message", {}).get("content", [])
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    return block.get("text", "")
                if "text" in block:
                    return block["text"]

    # {"assistantResponseEvent": {"content": "..."}}
    are = event.get("assistantResponseEvent", {})
    if are:
        c = are.get("content", "")
        if isinstance(c, str):
            return c

    # {"content": "..."}
    c = event.get("content", "")
    if isinstance(c, str) and c:
        return c

    # {"delta": {"text": "..."}}
    delta = event.get("delta", {})
    if delta:
        return delta.get("text", "")

    return ""
