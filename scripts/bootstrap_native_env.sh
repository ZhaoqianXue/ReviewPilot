#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_DIR="${ENV_DIR:-.venv-native}"
MACHINE="$($PYTHON_BIN -c 'import platform; print(platform.machine())')"

if [[ "$MACHINE" != "arm64" ]]; then
  echo "Refusing to create $ENV_DIR: $PYTHON_BIN reports $MACHINE; arm64 is required." >&2
  exit 2
fi

"$PYTHON_BIN" -m venv "$ENV_DIR"
"$ENV_DIR/bin/python" -m pip install --upgrade pip
"$ENV_DIR/bin/python" -m pip install -r requirements-dev.txt
"$ENV_DIR/bin/python" -c 'import platform; assert platform.machine() == "arm64", platform.machine()'
