#!/usr/bin/env bash
# Startup script for Square Cloud / any Linux host.
# Downloads and installs the headless kiro-cli (Linux x86_64) into
# $HOME/.local/bin (where kiro-cli-chat *must* live — kiro-cli expects
# its helper binaries at that path and refuses to start otherwise).

set -e

# Square Cloud's container has writable $PWD but $HOME varies — pin it
# to the app dir so the kiro-cli runtime files (.kiro, .aws, etc.) survive.
export HOME="${HOME:-/application}"
HOME_BIN="${HOME}/.local/bin"
mkdir -p "${HOME_BIN}" \
         "${HOME}/.aws" "${HOME}/.kiro" "${HOME}/.local/share/kiro-cli" \
         "${HOME}/.config" "${HOME}/.cache"
export XDG_CONFIG_HOME="${HOME}/.config"
export XDG_DATA_HOME="${HOME}/.local/share"
export XDG_CACHE_HOME="${HOME}/.cache"
export KIRO_HOME="${HOME}/.kiro"

KIRO_CLI_BIN="${HOME_BIN}/kiro-cli"
KIRO_CLI_CHAT="${HOME_BIN}/kiro-cli-chat"
KIRO_CLI_URL="https://desktop-release.q.us-east-1.amazonaws.com/latest/kirocli-x86_64-linux.zip"

# Re-download if any of the required binaries is missing
NEED_DOWNLOAD=0
for required in "${KIRO_CLI_BIN}" "${KIRO_CLI_CHAT}" "${HOME_BIN}/kiro-cli-term"; do
  if [ ! -f "${required}" ]; then
    NEED_DOWNLOAD=1
    break
  fi
done

if [ "${NEED_DOWNLOAD}" = "1" ]; then
  echo "[start] kiro-cli (or helpers) missing, downloading..."
  TMP_ZIP="/tmp/kirocli.zip"
  TMP_DIR="/tmp/kirocli_extract"
  rm -rf "${TMP_DIR}"
  mkdir -p "${TMP_DIR}"

  curl -fSL "${KIRO_CLI_URL}" -o "${TMP_ZIP}"
  unzip -q -o "${TMP_ZIP}" -d "${TMP_DIR}"

  # The zip extracts to kirocli/bin/{kiro-cli, kiro-cli-chat, kiro-cli-term, q, qchat}
  SRC_DIR="${TMP_DIR}/kirocli/bin"
  if [ ! -d "${SRC_DIR}" ]; then
    SRC_DIR="$(dirname "$(find "${TMP_DIR}" -type f -name 'kiro-cli' | head -n1)")"
  fi

  echo "[start] installing binaries into ${HOME_BIN}"
  for f in "${SRC_DIR}"/*; do
    if [ -f "${f}" ]; then
      install -m 755 "${f}" "${HOME_BIN}/"
    fi
  done
  rm -rf "${TMP_ZIP}" "${TMP_DIR}"
fi

echo "[start] installed binaries:"
ls -la "${HOME_BIN}/" | head -10

# Install a stripped-down agent with no tools enabled. The gateway uses
# this via `--agent proxy_only` so kiro-cli is forced to act as a pure
# chat model — no fs_read of /application, no execute_bash, no file
# writes on the server. The client's IDE handles all that locally.
PROXY_AGENT_FILE="${HOME}/.kiro/agents/proxy_only.json"
mkdir -p "$(dirname "${PROXY_AGENT_FILE}")"
cat > "${PROXY_AGENT_FILE}" <<'JSON'
{
  "name": "proxy_only",
  "description": "Pure-chat agent: no tools, no filesystem, no shell.",
  "prompt": null,
  "mcpServers": {},
  "tools": [],
  "toolAliases": {},
  "allowedTools": [],
  "resources": [],
  "hooks": {},
  "toolsSettings": {},
  "includeMcpJson": false,
  "model": null
}
JSON
echo "[start] installed proxy_only agent at ${PROXY_AGENT_FILE}"

export KIRO_CLI_PATH="${KIRO_CLI_BIN}"
echo "[start] KIRO_CLI_PATH=${KIRO_CLI_PATH}"
echo "[start] HOME=${HOME}"

# Smoke-test: --version must succeed; if not, kiro-cli has a runtime
# dependency we still need to satisfy (e.g. glibc version mismatch).
if ! "${KIRO_CLI_BIN}" --version >/tmp/cli_version.log 2>&1; then
  echo "[start] WARNING: kiro-cli --version failed:"
  cat /tmp/cli_version.log
fi

PORT="${PORT:-80}"
echo "[start] launching gateway on port ${PORT}..."
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT}"
