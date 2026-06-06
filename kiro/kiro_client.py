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
import uuid
from typing import AsyncIterator, Dict, List, Optional, Tuple

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Constantes de endpoints
# ---------------------------------------------------------------------------

REGION = "us-east-1"

# Endpoint de chat — AWS CodeWhisperer via runtime.kiro.dev
KIRO_API_URL = f"https://runtime.{REGION}.kiro.dev/generateAssistantResponse"
# Endpoint para listar modelos e obter profileArn
KIRO_MODELS_URL = f"https://runtime.{REGION}.kiro.dev/ListAvailableModels"

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
# Headers AWS CodeWhisperer
# ---------------------------------------------------------------------------

def _build_headers(ksk_key: str) -> dict:
    """
    Constrói os headers necessários para chamar runtime.kiro.dev.
    A ksk_ key é usada diretamente como Bearer token.
    """
    import hashlib
    import socket
    import getpass
    fingerprint = hashlib.sha256(
        f"{socket.gethostname()}-{getpass.getuser()}-kiro-gateway".encode()
    ).hexdigest()

    return {
        "Authorization": f"Bearer {ksk_key}",
        "Content-Type": "application/x-amz-json-1.0",
        "x-amz-target": "AmazonCodeWhispererStreamingService.GenerateAssistantResponse",
        "User-Agent": (
            f"aws-sdk-js/1.0.27 ua/2.1 os/linux#5.0 lang/js md/nodejs#22.0.0 "
            f"api/codewhispererstreaming#1.0.27 m/E KiroIDE-0.7.45-{fingerprint}"
        ),
        "x-amz-user-agent": f"aws-sdk-js/1.0.27 KiroIDE-0.7.45-{fingerprint}",
        "x-amzn-codewhisperer-optout": "true",
        "x-amzn-kiro-agent-mode": "vibe",
        "amz-sdk-invocation-id": str(uuid.uuid4()),
        "amz-sdk-request": "attempt=1; max=3",
    }


async def fetch_profile_arn(ksk_key: str) -> Optional[str]:
    """
    Busca o profileArn da conta via GET /ListAvailableModels.
    Retorna None se não encontrar.
    """
    headers = _build_headers(ksk_key)
    # Usa o header correto para ListAvailableModels
    list_headers = dict(headers)
    list_headers["x-amz-target"] = "AmazonQDeveloperStreamingService.ListAvailableModels"
    list_headers.pop("x-amzn-kiro-agent-mode", None)
    list_headers.pop("x-amzn-codewhisperer-optout", None)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(KIRO_MODELS_URL, headers=list_headers, timeout=15)
            if resp.status_code != 200:
                logger.debug(f"ListAvailableModels returned {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            # Tenta extrair profileArn direto
            profile_arn = data.get("profileArn")
            if profile_arn:
                return profile_arn
            # Tenta extrair do ARN dos modelos
            models = data.get("models", [])
            for m in models:
                arn = m.get("modelArn") or m.get("profileArn")
                if arn and "codewhisperer" in arn:
                    parts = arn.split(":")
                    if len(parts) >= 6:
                        return ":".join(parts[:6])
            return None
        except Exception as e:
            logger.warning(f"fetch_profile_arn failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Verificação de chave (para o painel admin)
# ---------------------------------------------------------------------------

async def verify_ksk_key(ksk_key: str) -> Optional[str]:
    """
    Verifica se uma ksk_ key é válida fazendo uma chamada mínima ao Kiro.
    Retorna "valid" em caso de sucesso, None em caso de falha.
    """
    # Tenta uma request mínima para verificar a chave
    payload = build_kiro_payload(
        messages=[{"role": "user", "content": "hi"}],
        system=None,
        model_id="claude-sonnet-4.5",
        profile_arn=None,
    )
    headers = _build_headers(ksk_key)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                KIRO_API_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
            if resp.status_code in (200, 400):
                # 400 pode ser formato inválido mas autenticação OK
                return "valid"
            if resp.status_code == 403:
                return None
            return "valid"
        except Exception as e:
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
    profile_arn: Optional[str] = None,
) -> AsyncIterator[str]:
    """
    Envia uma request ao Kiro e retorna um async iterator de chunks de texto.
    A ksk_ key é usada diretamente como Bearer token.
    """
    model_id = resolve_model_id(model)
    payload = build_kiro_payload(messages, system, model_id, profile_arn)
    headers = _build_headers(ksk_key)

    async with client.stream(
        "POST",
        KIRO_API_URL,
        content=json.dumps(payload).encode(),
        headers=headers,
        timeout=httpx.Timeout(connect=30.0, read=REQUEST_TIMEOUT, write=30.0, pool=30.0),
    ) as resp:
        if resp.status_code == 403:
            raise KiroAPIError("Invalid or expired ksk_ key (403)", status_code=403)

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
    """
    Versão não-streaming: coleta toda a resposta e retorna (text, in_tokens, out_tokens).
    """
    parts = []
    in_tokens = 0
    out_tokens = 0

    async for chunk in call_kiro_streaming(
        account_id, ksk_key, messages, system, model, client, profile_arn
    ):
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
