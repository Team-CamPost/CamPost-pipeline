#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

ensure_env_file() {
  if [[ -f .env ]]; then
    return
  fi

  if [[ ! -f .env.example ]]; then
    echo "[ERROR] .env not found and .env.example is missing."
    echo "        Create .env manually before running pipeline."
    exit 1
  fi

  cp .env.example .env
  echo "[INFO] Created .env from .env.example"
}

get_env_value() {
  local key="$1"
  grep -E "^${key}=" .env | head -n1 | cut -d '=' -f2- || true
}

validate_env_keys() {
  local missing=()
  local key value

  for key in "$@"; do
    value="$(get_env_value "$key")"
    if [[ -z "$value" || "$value" == "<REQUIRED>" ]]; then
      missing+=("$key")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "[ERROR] .env is missing required values: ${missing[*]}"
    echo "        Fill .env first, then rerun this script."
    exit 1
  fi
}

resolve_python() {
  if [[ -x .venv/Scripts/python ]]; then
    echo ".venv/Scripts/python"
    return
  fi

  if [[ -x .venv/bin/python ]]; then
    echo ".venv/bin/python"
    return
  fi

  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi

  echo "[ERROR] python executable not found."
  echo "        Create .venv and install requirements first."
  exit 1
}

ensure_env_file
validate_env_keys POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
PYTHON_BIN="$(resolve_python)"

set -a
# shellcheck disable=SC1091
source .env
set +a

exec "$PYTHON_BIN" main.py
