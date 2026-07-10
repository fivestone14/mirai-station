#!/usr/bin/env bash
# One-shot installer for mirai-watch. Idempotent. Run once per Mac mini after cloning the plugin.
set -euo pipefail

# Load shared env (resolves MIRAI_STATION_ROOT, MIRAI_STATION_VENV).
# env.sh tries to read secrets from Keychain; missing secrets must not abort the installer.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set +e
source "${SCRIPT_DIR}/env.sh"
set -e

echo "==> mirai-watch setup starting"
echo "    plugin root: ${MIRAI_STATION_ROOT}"
echo "    venv:        ${MIRAI_STATION_VENV}"

# ---------- Pick a Python 3.10+ interpreter ----------
# The watch orchestrator tolerates 3.9, but the market-data client (schwab-py)
# needs 3.10+. We prefer the newest 3.10+ found so ONE venv serves the whole
# system. Override the choice by exporting MIRAI_STATION_PYTHON=/path/to/python.
MIN_MAJOR=3; MIN_MINOR=10
_is_new_enough() {  # $1 = python binary -> exit 0 if >= 3.10
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= ('"${MIN_MAJOR}"','"${MIN_MINOR}"') else 1)' 2>/dev/null
}
pick_python() {
  if [[ -n "${MIRAI_STATION_PYTHON:-}" ]]; then
    _is_new_enough "${MIRAI_STATION_PYTHON}" && { echo "${MIRAI_STATION_PYTHON}"; return 0; }
    return 1
  fi
  local cand bin
  for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
    bin="$(command -v "$cand" 2>/dev/null)" || continue
    _is_new_enough "$bin" && { echo "$bin"; return 0; }
  done
  return 1
}

if ! PYTHON_BIN="$(pick_python)"; then
  echo "ERROR: need Python ${MIN_MAJOR}.${MIN_MINOR}+ for the market-data client (schwab-py)," >&2
  echo "       but none was found. Default python3 is: $(python3 --version 2>&1)." >&2
  echo "       Fix: install a newer Python (e.g. 'brew install python@3.12')," >&2
  echo "       or set MIRAI_STATION_PYTHON=/path/to/python3.12 and re-run." >&2
  exit 1
fi
echo "    python:      ${PYTHON_BIN} ($(${PYTHON_BIN} --version 2>&1))"

# Create venv if missing; if one already exists, make sure it is new enough.
if [[ ! -x "${MIRAI_STATION_VENV}/bin/python" ]]; then
  echo "==> creating venv with ${PYTHON_BIN}"
  "${PYTHON_BIN}" -m venv "${MIRAI_STATION_VENV}"
elif ! _is_new_enough "${MIRAI_STATION_VENV}/bin/python"; then
  echo "ERROR: existing venv is $(${MIRAI_STATION_VENV}/bin/python --version 2>&1)," >&2
  echo "       too old for schwab-py (needs ${MIN_MAJOR}.${MIN_MINOR}+). Recreate it:" >&2
  echo "         rm -rf '${MIRAI_STATION_VENV}' && bash '${BASH_SOURCE[0]}'" >&2
  exit 1
fi

# Install/upgrade deps
echo "==> installing watch dependencies"
"${MIRAI_STATION_VENV}/bin/pip" install --upgrade pip >/dev/null
"${MIRAI_STATION_VENV}/bin/pip" install -r "${MIRAI_STATION_ROOT}/runtime/watch/requirements.txt"
# Layer-2 LOB (FLOW) sensor box — editable install so the tick + collector import it
"${MIRAI_STATION_VENV}/bin/pip" install -e "${MIRAI_STATION_ROOT}/skills/lob-flow"

# Ensure state dirs exist
mkdir -p "${MIRAI_STATION_STATE}/logs" "${MIRAI_STATION_STATE}/locks"

# Verify LangGraph imports cleanly
"${MIRAI_STATION_VENV}/bin/python" -c "
from langgraph.graph import StateGraph
from langgraph.checkpoint.sqlite import SqliteSaver
import importlib.metadata as m
print('langgraph', m.version('langgraph'), 'ok')
"

echo "==> mirai-watch setup complete"
echo "    next: register the plugin's watch CLI on PATH (env.sh adds it) and run \`mirai-watch doctor\` to smoke test"
