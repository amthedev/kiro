# -*- coding: utf-8 -*-
"""
kiro-cli subprocess runner.

Each Kiro account is a `ksk_` API key. We drive the official kiro-cli binary
in non-interactive mode, passing the key via the KIRO_API_KEY env var, and
clean its ANSI/credits output into plain text.
"""

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

# Strip ANSI escape sequences (colors, cursor moves, etc.)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[=>]")

# Lines we never want in the final answer
_NOISE_MARKERS = (
    "Credits:",
    "WARNING:",
    "Failed to retrieve MCP",
    "Try running `kiro-cli login`",
    "MCP functionality disabled",
)


def get_kirocli_path() -> Optional[str]:
    """
    Locate the kiro-cli binary.

    Priority:
    1. KIRO_CLI_PATH env var
    2. ./bin/kiro-cli (downloaded at deploy on Linux)
    3. kiro-cli on PATH
    """
    env_path = os.getenv("KIRO_CLI_PATH")
    if env_path and Path(env_path).exists():
        return env_path

    local = Path("bin/kiro-cli")
    if local.exists():
        return str(local.resolve())

    found = shutil.which("kiro-cli")
    if found:
        return found

    return None


def clean_output(raw: str) -> str:
    """Remove ANSI codes and CLI noise lines, return the model's answer."""
    text = _ANSI_RE.sub("", raw)
    kept = []
    for line in text.split("\n"):
        stripped = line.strip()
        if any(marker in stripped for marker in _NOISE_MARKERS):
            continue
        # kiro-cli prefixes the assistant reply with "> "
        if stripped.startswith("> "):
            stripped = stripped[2:]
        kept.append(stripped)
    # Collapse leading/trailing blank lines
    result = "\n".join(kept).strip()
    return result


class KiroCliError(Exception):
    pass


async def run_chat(api_key: str, prompt: str, timeout: float = 180.0) -> str:
    """
    Send `prompt` to kiro-cli using `api_key` (a ksk_ key).

    Returns the cleaned plain-text answer.
    Raises KiroCliError on failure (bad key, quota, binary missing).
    """
    binary = get_kirocli_path()
    if not binary:
        raise KiroCliError(
            "kiro-cli binary not found. Set KIRO_CLI_PATH or place it in ./bin/kiro-cli"
        )

    env = os.environ.copy()
    env["KIRO_API_KEY"] = api_key
    env["NO_COLOR"] = "1"

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "chat", "--no-interactive",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except Exception as e:
        raise KiroCliError(f"Failed to spawn kiro-cli: {e}")

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise KiroCliError(f"kiro-cli timed out after {timeout}s")

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace")
        raise KiroCliError(f"kiro-cli exited {proc.returncode}: {err[:500]}")

    answer = clean_output(stdout.decode("utf-8", errors="replace"))
    if not answer:
        err = stderr.decode("utf-8", errors="replace")
        raise KiroCliError(f"kiro-cli returned empty answer. stderr: {err[:300]}")

    return answer


async def verify_key(api_key: str, timeout: float = 30.0) -> Optional[str]:
    """
    Check whether a ksk_ key is valid by running `kiro-cli whoami`.

    Returns the account email on success, None on failure.
    """
    binary = get_kirocli_path()
    if not binary:
        return None

    env = os.environ.copy()
    env["KIRO_API_KEY"] = api_key
    env["NO_COLOR"] = "1"

    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "whoami",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except Exception as e:
        logger.warning(f"verify_key failed: {e}")
        return None

    out = clean_output(stdout.decode("utf-8", errors="replace"))
    if proc.returncode == 0 and "Email:" in out:
        for line in out.split("\n"):
            if line.startswith("Email:"):
                return line.split("Email:", 1)[1].strip()
    return None
