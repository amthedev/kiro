#!/usr/bin/env bash
# Startup script for Square Cloud / any Linux host.
# Downloads the headless kiro-cli (Linux x86_64) on first boot, then starts the gateway.

set -e

KIRO_CLI_DIR="bin"
KIRO_CLI_BIN="${KIRO_CLI_DIR}/kiro-cli"
KIRO_CLI_URL="https://desktop-release.q.us-east-1.amazonaws.com/latest/kirocli-x86_64-linux.zip"

mkdir -p "${KIRO_CLI_DIR}"

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

  # Find the kiro-cli executable inside the extracted tree
  FOUND="$(find "${TMP_DIR}" -type f -name 'kiro-cli' | head -n1)"
  if [ -z "${FOUND}" ]; then
    # Some builds name it 'q' or place it under bin/
    FOUND="$(find "${TMP_DIR}" -type f \( -name 'kiro-cli' -o -name 'q' \) | head -n1)"
  fi

  if [ -z "${FOUND}" ]; then
    echo "[start] ERROR: kiro-cli binary not found inside the zip."
    echo "[start] Contents:"
    find "${TMP_DIR}" -maxdepth 3 -type f | head -40
    exit 1
  fi

  cp "${FOUND}" "${KIRO_CLI_BIN}"
  chmod +x "${KIRO_CLI_BIN}"
  rm -rf "${TMP_ZIP}" "${TMP_DIR}"
  echo "[start] kiro-cli installed at ${KIRO_CLI_BIN}"
else
  echo "[start] kiro-cli already present at ${KIRO_CLI_BIN}"
fi

export KIRO_CLI_PATH="$(pwd)/${KIRO_CLI_BIN}"
echo "[start] KIRO_CLI_PATH=${KIRO_CLI_PATH}"

# Start the gateway (Square Cloud uses port 80)
PORT="${PORT:-80}"
echo "[start] launching gateway on port ${PORT}..."
exec python3 -m uvicorn main:app --host 0.0.0.0 --port "${PORT}"
