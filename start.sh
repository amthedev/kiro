#!/usr/bin/env bash
# Startup script for Square Cloud / any Linux host.
# Downloads the headless kiro-cli (Linux x86_64) on first boot, then starts the gateway.

set -e

KIRO_CLI_DIR="bin"
KIRO_CLI_BIN="${KIRO_CLI_DIR}/kiro-cli"
KIRO_CLI_URL="https://desktop-release.q.us-east-1.amazonaws.com/latest/kirocli-x86_64-linux.zip"

mkdir -p "${KIRO_CLI_DIR}"

# Force re-download if we only have kiro-cli but not the chat helper.
# Old deploys (before this fix) extracted only the launcher.
if [ -f "${KIRO_CLI_BIN}" ] && [ ! -f "${KIRO_CLI_DIR}/kiro-cli-chat" ]; then
  echo "[start] kiro-cli-chat helper missing, forcing re-download..."
  rm -f "${KIRO_CLI_BIN}"
fi

if [ ! -f "${KIRO_CLI_BIN}" ]; then
  echo "[start] kiro-cli not found, downloading Linux build..."
  TMP_ZIP="/tmp/kirocli.zip"

  # Download (zip contains the kiro-cli binary + helpers)
  curl -fSL "${KIRO_CLI_URL}" -o "${TMP_ZIP}"

  echo "[start] extracting..."
  # Extract into a temp dir, then locate the kiro-cli binary
  TMP_DIR="/tmp/kirocli_extract"
  rm -rf "${TMP_DIR}"
  mkdir -p "${TMP_DIR}"
  unzip -q "${TMP_ZIP}" -d "${TMP_DIR}"

  # The zip ships kiro-cli AND helper binaries (kiro-cli-chat, etc.) that
  # kiro-cli spawns at runtime. Copy them all so they stay co-located.
  FOUND_MAIN="$(find "${TMP_DIR}" -type f -name 'kiro-cli' | head -n1)"
  if [ -z "${FOUND_MAIN}" ]; then
    echo "[start] ERROR: kiro-cli binary not found inside the zip."
    echo "[start] Contents:"
    find "${TMP_DIR}" -maxdepth 3 -type f | head -40
    exit 1
  fi

  SRC_DIR="$(dirname "${FOUND_MAIN}")"
  echo "[start] copying binaries from ${SRC_DIR}"
  # Copy every regular file from the same directory as kiro-cli
  for f in "${SRC_DIR}"/*; do
    if [ -f "${f}" ]; then
      cp "${f}" "${KIRO_CLI_DIR}/"
      chmod +x "${KIRO_CLI_DIR}/$(basename "${f}")" 2>/dev/null || true
    fi
  done
  rm -rf "${TMP_ZIP}" "${TMP_DIR}"
  echo "[start] kiro-cli + helpers installed in ${KIRO_CLI_DIR}/:"
  ls -la "${KIRO_CLI_DIR}/" | head -10
else
  echo "[start] kiro-cli already present at ${KIRO_CLI_BIN}"
fi

export KIRO_CLI_PATH="$(pwd)/${KIRO_CLI_BIN}"
echo "[start] KIRO_CLI_PATH=${KIRO_CLI_PATH}"

# kiro-cli needs writable HOME, XDG dirs, and ~/.aws / ~/.kiro
# On Square Cloud the default HOME may be unset or read-only.
export HOME="${HOME:-$(pwd)/runtime_home}"
mkdir -p "${HOME}/.aws" "${HOME}/.kiro" "${HOME}/.local/share/kiro-cli" \
         "${HOME}/.config" "${HOME}/.cache"
export XDG_CONFIG_HOME="${HOME}/.config"
export XDG_DATA_HOME="${HOME}/.local/share"
export XDG_CACHE_HOME="${HOME}/.cache"
export KIRO_HOME="${HOME}/.kiro"
echo "[start] HOME=${HOME}"

# Start the gateway (Square Cloud uses port 80)
PORT="${PORT:-80}"
echo "[start] launching gateway on port ${PORT}..."
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT}"
