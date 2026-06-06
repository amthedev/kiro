# -*- coding: utf-8 -*-
"""
Kiro HTTP client — substitui o kiro-cli por chamadas HTTP diretas.

Fluxo por conta (ksk_):
  1. ksk_ key → POST /exchangeToken → { accessToken, refreshToken, expiresAt }
  2. accessToken → POST runtime.{region}.kiro.dev/generateAssistantResponse
  3. accessToken expira ~1h → refresh automático via /refreshToken

O módulo é stateful por conta: cada KiroAccount mantém seus próprios
tokens em memória e renova quando necessário.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Constantes de endpoints
# ---------------------------------------------------------------------------

REGION = "us-east-1"

# Trocar ksk_ por tokens OAuth
TOKEN_EXCHANGE_URL = f"https://prod.{REGION}.auth.desktop.kiro.dev/exchangeToken"
# Renovar access token usando refresh token
TOKEN_REFRESH_URL = f"https://prod.{REGION}.auth.desktop.kiro.dev/refreshToken"
# Endpoint de chat
KIRO_API_URL = f"https://runtime.{REGION}.kiro.dev/generateAssistantResponse"

# Renova quando faltam 5 minutos para expirar
REFRESH_THRESHOLD_SECONDS = 300
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
# Estado de autenticação por conta
# ---------------------------------------------------------------------------

class AccountTokens:
    """Tokens OAuth para uma conta ksk_."""

    def __init__(self):
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.profile_arn: Optional[str] = None
        self.expires_at: Optional[datetime] = None
        self._lock = asyncio.Lock()

    def is_expiring_soon(self) -> bool:
        if not self.expires_at or not self.access_token:
            return True
        now = datetime.now(timezone.utc)
        return (self.expires_at - now).total_seconds() < REFRESH_THRESHOLD_SECONDS

    def is_expired(self) -> bool:
        if not self.expires_at:
            return True
        return datetime.now(timezone.utc) >= self.expires_at


# Dicionário global: account_id → AccountTokens
_account_tokens: Dict[int, AccountTokens] = {}
_tokens_lock = asyncio.Lock()


def _get_or_create_tokens(account_id: int) -> AccountTokens:
    if account_id not in _account_tokens:
        _account_tokens[account_id] = AccountTokens()
    return _account_tokens[account_id]


def evict_account_tokens(account_id: int) -> None:
    """Remove tokens de uma conta (ex: quando ela é deletada)."""
    _account_tokens.pop(account_id, None)


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

def _parse_expires_at(value: Optional[str], expires_in: Optional[int]) -> datetime:
    """Converte expiresAt (ISO 8601) ou expiresIn (segundos) para datetime UTC."""
    if value:
        try:
            s = value.replace("Z", "+00:00")
            return datetime.fromisoformat(s)
        except Exception:
            pass
    if expires_in:
        return datetime.now(timezone.utc) + timedelta(seconds=expires_in - 60)
    # Fallback: 1 hora
    return datetime.now(timezone.utc) + timedelta(hours=1)


async def _exchange_ksk_token(ksk_key: str, client: httpx.AsyncClient) -> AccountTokens:
    """
    Troca uma ksk_ key por access/refresh tokens.
    POST /exchangeToken com body {"apiKey": "ksk_..."}
    """
    payload = {"apiKey": ksk_key}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "KiroIDE/1.0",
    }
    try:
        resp = await client.post(TOKEN_EXCHANGE_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise KiroAuthError(f"Token exchange failed ({e.response.status_code}): {e.response.text[:300]}")
    except Exception as e:
        raise KiroAuthError(f"Token exchange request failed: {e}")

    access_token = data.get("accessToken")
    if not access_token:
        raise KiroAuthError(f"No accessToken in exchange response: {data}")

    tokens = AccountTokens()
    tokens.access_token = access_token
    tokens.refresh_token = data.get("refreshToken")
    tokens.profile_arn = data.get("profileArn")
    tokens.expires_at = _parse_expires_at(data.get("expiresAt"), data.get("expiresIn"))
    return tokens


async def _refresh_access_token(tokens: AccountTokens, client: httpx.AsyncClient) -> None:
    """
    Renova o access token usando o refresh token.
    POST /refreshToken com body {"refreshToken": "..."}
    """
    if not tokens.refresh_token:
        raise KiroAuthError("No refresh token available — need to re-exchange ksk_ key")

    payload = {"refreshToken": tokens.refresh_token}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "KiroIDE/1.0",
    }
    try:
        resp = await client.post(TOKEN_REFRESH_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        raise KiroAuthError(f"Token refresh failed ({e.response.status_code}): {e.response.text[:300]}")
    except Exception as e:
        raise KiroAuthError(f"Token refresh request failed: {e}")

    new_access = data.get("accessToken")
    if not new_access:
        raise KiroAuthError(f"No accessToken in refresh response: {data}")

    tokens.access_token = new_access
    if data.get("refreshToken"):
        tokens.refresh_token = data["refreshToken"]
    if data.get("profileArn"):
        tokens.profile_arn = data["profileArn"]
    tokens.expires_at = _parse_expires_at(data.get("expiresAt"), data.get("expiresIn"))
    logger.debug(f"Token refreshed, expires at {tokens.expires_at.isoformat()}")


async def get_valid_access_token(
    account_id: int,
    ksk_key: str,
    client: httpx.AsyncClient,
) -> Tuple[str, Optional[str]]:
    """
    Retorna (access_token, profile_arn) válidos para a conta.
    Faz exchange/refresh automaticamente quando necessário.
    Thread-safe via asyncio.Lock por conta.
    """
    tokens = _get_or_create_tokens(account_id)

    async with tokens._lock:
        # Token ainda válido
        if tokens.access_token and not tokens.is_expiring_soon():
            return tokens.access_token, tokens.profile_arn

        # Tenta refresh se tem refresh token
        if tokens.refresh_token and not tokens.is_expired():
            try:
                await _refresh_access_token(tokens, client)
                return tokens.access_token, tokens.profile_arn
            except KiroAuthError as e:
                logger.warning(f"Refresh failed for account {account_id}: {e}, re-exchanging...")

        # Exchange com a ksk_ key
        new_tokens = await _exchange_ksk_token(ksk_key, client)
        tokens.access_token = new_tokens.access_token
        tokens.refresh_token = new_tokens.refresh_token
        tokens.profile_arn = new_tokens.profile_arn
        tokens.expires_at = new_tokens.expires_at
        logger.info(f"Account {account_id}: token exchanged, expires {tokens.expires_at.isoformat()}")
        return tokens.access_token, tokens.profile_arn


# ---------------------------------------------------------------------------
# Verificação de chave (para o painel admin)
# ---------------------------------------------------------------------------

async def verify_ksk_key(ksk_key: str) -> Optional[str]:
    """
    Verifica se uma ksk_ key é válida fazendo token exchange.
    Retorna o profile_arn em caso de sucesso, None em caso de falha.
    """
    async with httpx.AsyncClient() as client:
        try:
            tokens = await _exchange_ksk_token(ksk_key, client)
            return tokens.profile_arn or "valid"
        except KiroAuthError as e:
            logger.warning(f"verify_ksk_key failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Construção do payload Kiro
# ---------------------------------------------------------------------------

def _content_to_text(content) -> str:
    """Extrai texto de conteúdo no formato Anthropic/OpenAI."""
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
        return "\n".join(parts)
    return str(content) if content else ""


def build_kiro_payload(
    messages: List[dict],
    system: Optional[str],
    model_id: str,
    profile_arn: Optional[str],
) -> dict:
    """
    Constrói o payload para POST /generateAssistantResponse.

    O formato Kiro espera:
    - conversationState.currentMessage.userInputMessage: última mensagem do usuário
    - conversationState.history: mensagens anteriores alternando user/assistant
    - profileArn: ARN do perfil CodeWhisperer
    """
    conversation_id = str(uuid.uuid4())

    # Normaliza as mensagens: extrai texto e resolve system prompt
    normalized: List[dict] = []
    if system:
        # Adiciona system prompt como prefixo da primeira mensagem de usuário
        normalized.append({"role": "user", "text": _content_to_text(system)})
        normalized.append({"role": "assistant", "text": "Understood."})

    for msg in messages:
        role = msg.get("role", "user")
        text = _content_to_text(msg.get("content", ""))
        if role == "system":
            # System messages dentro de messages[] são tratados como user
            normalized.append({"role": "user", "text": text})
        else:
            normalized.append({"role": role, "text": text})

    # Garante que começa com user e alterna corretamente
    normalized = _normalize_alternating(normalized)

    if not normalized:
        normalized = [{"role": "user", "text": "(empty)"}]

    # A última mensagem deve ser do usuário
    if normalized[-1]["role"] != "user":
        normalized.append({"role": "user", "text": "(continue)"})

    # Separa histórico (todas menos a última) da mensagem atual
    history_msgs = normalized[:-1]
    current_text = normalized[-1]["text"] or "(empty)"

    # Constrói histórico no formato Kiro
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

    # Monta o payload
    payload: dict = {
        "conversationState": {
            "chatTriggerType": "MANUAL",
            "conversationId": conversation_id,
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


def _normalize_alternating(messages: List[dict]) -> List[dict]:
    """Garante alternância user/assistant, mesclando consecutivos do mesmo papel."""
    if not messages:
        return []

    result = []
    for msg in messages:
        if result and result[-1]["role"] == msg["role"]:
            # Mescla com a última mensagem do mesmo role
            sep = "\n\n" if result[-1]["text"] and msg["text"] else ""
            result[-1]["text"] = result[-1]["text"] + sep + msg["text"]
        else:
            result.append({"role": msg["role"], "text": msg["text"]})

    return result


# ---------------------------------------------------------------------------
# Mapeamento de modelos
# ---------------------------------------------------------------------------

_MODEL_MAP: Dict[str, str] = {
    "claude-opus-4-5":              "claude-opus-4.5",
    "claude-opus-4-5-20251101":     "claude-opus-4.5",
    "claude-haiku-4-5":             "claude-haiku-4.5",
    "claude-haiku-4.5":             "claude-haiku-4.5",
    "claude-sonnet-4-5":            "CLAUDE_SONNET_4_5_20250929_V1_0",
    "claude-sonnet-4-5-20250929":   "CLAUDE_SONNET_4_5_20250929_V1_0",
    "claude-sonnet-4":              "CLAUDE_SONNET_4_20250514_V1_0",
    "claude-sonnet-4-20250514":     "CLAUDE_SONNET_4_20250514_V1_0",
    "claude-3-7-sonnet-20250219":   "CLAUDE_3_7_SONNET_20250219_V1_0",
    "auto":                         "claude-sonnet-4.5",
}


def resolve_model_id(model_name: str) -> str:
    """Converte nome externo para internal Kiro model ID."""
    return _MODEL_MAP.get(model_name, model_name)


# ---------------------------------------------------------------------------
# Chamada HTTP ao Kiro com streaming
# ---------------------------------------------------------------------------

async def call_kiro_streaming(
    account_id: int,
    ksk_key: str,
    messages: List[dict],
    system: Optional[str],
    model: str,
    client: httpx.AsyncClient,
) -> AsyncIterator[str]:
    """
    Envia uma request ao Kiro e retorna um async iterator de chunks de texto.

    Yields strings de texto conforme chegam do servidor (SSE).
    Raises KiroAPIError em caso de falha.
    """
    access_token, profile_arn = await get_valid_access_token(account_id, ksk_key, client)
    model_id = resolve_model_id(model)
    payload = build_kiro_payload(messages, system, model_id, profile_arn)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "KiroIDE/1.0",
    }

    async with client.stream(
        "POST",
        KIRO_API_URL,
        json=payload,
        headers=headers,
        timeout=httpx.Timeout(connect=30.0, read=REQUEST_TIMEOUT, write=30.0, pool=30.0),
    ) as resp:
        if resp.status_code == 403:
            # Token expirou no meio — force refresh e levanta erro para retry
            async with _get_or_create_tokens(account_id)._lock:
                _get_or_create_tokens(account_id).access_token = None
            raise KiroAPIError("Token expired (403)", status_code=403)

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
) -> Tuple[str, int, int]:
    """
    Versão não-streaming: coleta toda a resposta e retorna (text, in_tokens, out_tokens).
    """
    parts = []
    in_tokens = 0
    out_tokens = 0

    async for chunk in call_kiro_streaming(account_id, ksk_key, messages, system, model, client):
        parts.append(chunk)

    text = "".join(parts)
    # Estimativa simples de tokens (~4 chars/token)
    out_tokens = max(1, len(text) // 4)
    return text, in_tokens, out_tokens


# ---------------------------------------------------------------------------
# Parser de SSE do Kiro
# ---------------------------------------------------------------------------

async def _parse_sse_stream(response: httpx.Response) -> AsyncIterator[str]:
    """
    Parseia o stream SSE do Kiro e extrai chunks de texto.

    O Kiro usa o formato AWS Event Stream. Cada evento SSE contém JSON
    com o campo de texto da resposta.
    """
    buffer = ""

    async for raw_line in response.aiter_lines():
        line = raw_line.strip()
        if not line:
            continue

        # O Kiro envia linhas no formato "data: <json>"
        if line.startswith("data:"):
            data_str = line[5:].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                # Às vezes o JSON vem fragmentado — acumula no buffer
                buffer += data_str
                try:
                    data = json.loads(buffer)
                    buffer = ""
                except json.JSONDecodeError:
                    continue

            text = _extract_text_from_event(data)
            if text:
                yield text

        # Formato alternativo: linha JSON direta (sem prefixo "data:")
        elif line.startswith("{"):
            buffer += line
            try:
                data = json.loads(buffer)
                buffer = ""
                text = _extract_text_from_event(data)
                if text:
                    yield text
            except json.JSONDecodeError:
                pass


def _extract_text_from_event(event: dict) -> str:
    """Extrai o texto de um evento SSE do Kiro."""
    # Formato 1: {"output": {"message": {"content": [{"text": "..."}]}}}
    output = event.get("output", {})
    if output:
        msg = output.get("message", {})
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
                if isinstance(block, dict) and "text" in block:
                    return block["text"]

    # Formato 2: {"assistantResponseEvent": {"content": "..."}}
    are = event.get("assistantResponseEvent", {})
    if are:
        content = are.get("content", "")
        if isinstance(content, str):
            return content

    # Formato 3: {"content": "..."}
    content = event.get("content", "")
    if isinstance(content, str) and content:
        return content

    # Formato 4: {"delta": {"text": "..."}} (streaming incremental)
    delta = event.get("delta", {})
    if delta:
        return delta.get("text", "")

    return ""
