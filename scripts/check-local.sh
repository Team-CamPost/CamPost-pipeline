#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

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
	echo "        Create .venv and install dependencies first."
	exit 1
}

resolve_pre_commit() {
	if [[ -x .venv/Scripts/pre-commit ]]; then
		echo ".venv/Scripts/pre-commit"
		return
	fi

	if [[ -x .venv/bin/pre-commit ]]; then
		echo ".venv/bin/pre-commit"
		return
	fi

	if command -v pre-commit >/dev/null 2>&1; then
		command -v pre-commit
		return
	fi

	echo "[ERROR] pre-commit executable not found."
	echo "        Run: pip install -e .[dev] && pre-commit install"
	exit 1
}

PYTHON_BIN="$(resolve_python)"
PRE_COMMIT_BIN="$(resolve_pre_commit)"

echo "[INFO] Running pipeline local checks..."
"$PRE_COMMIT_BIN" run --all-files
"$PYTHON_BIN" -m unittest discover -s tests -p "test_*.py"
