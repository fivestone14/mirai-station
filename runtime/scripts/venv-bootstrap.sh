#!/usr/bin/env bash
# Provision the mirai-station Python venv with schwab-py + scipy + deps.
# Idempotent: re-runs upgrade existing packages.

set -euo pipefail

# shellcheck source=env.sh
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

PY_VERSION="${MIRAI_STATION_PY:-python3}"

if [[ ! -d "$MIRAI_STATION_VENV" ]]; then
  log "Creating venv at $MIRAI_STATION_VENV"
  mkdir -p "$(dirname "$MIRAI_STATION_VENV")"
  "$PY_VERSION" -m venv "$MIRAI_STATION_VENV"
fi

# shellcheck source=/dev/null
source "$MIRAI_STATION_VENV/bin/activate"

pip install --upgrade pip wheel

# Pinned-ish: lock to majors we've tested against.
pip install --upgrade \
  'schwab-py>=1.5,<2' \
  'scipy>=1.13,<2' \
  'numpy>=1.26,<3' \
  'pandas>=2.2,<3' \
  'requests>=2.31' \
  'python-dateutil>=2.8' \
  'pytz>=2024.1' \
  'authlib>=1.3' \
  'httpx>=0.27' \
  'pyyaml>=6'

# Layer-2 LOB (FLOW) sensor box — editable install of the local package
pip install -e "${MIRAI_STATION_ROOT}/skills/lob-flow"

log "venv-bootstrap complete: $(python --version) at $MIRAI_STATION_VENV"

deactivate
