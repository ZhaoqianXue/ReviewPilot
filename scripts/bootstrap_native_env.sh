#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_DIR="${ENV_DIR:-$REPO_ROOT/.venv-native}"
MACHINE="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())')"

if [[ "$MACHINE" != "arm64" ]]; then
  echo "Refusing to create $ENV_DIR: $PYTHON_BIN reports $MACHINE; arm64 is required." >&2
  exit 2
fi

if [[ -e "$ENV_DIR" ]]; then
  echo "Refusing to create $ENV_DIR: it already exists; remove or rename it intentionally before rebuilding." >&2
  exit 3
fi

"$PYTHON_BIN" -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --upgrade pip
"$ENV_DIR/bin/python" -m pip install -r "$REPO_ROOT/requirements-dev.txt"
ENV_MACHINE="$("$ENV_DIR/bin/python" -c 'import platform; print(platform.machine())')"
if [[ "$ENV_MACHINE" != "arm64" ]]; then
  echo "Refusing to use $ENV_DIR: created environment reports $ENV_MACHINE; arm64 is required." >&2
  exit 4
fi
