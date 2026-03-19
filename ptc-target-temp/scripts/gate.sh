#!/usr/bin/env bash
set -euo pipefail

# Trap: explicit exit with context on first failure
trap 'echo ""; echo "❌ GATE FAILED at line $LINENO: $BASH_COMMAND"; echo "Exit code: $?"; exit 1' ERR

TARGET="${1:-python}"

section () { echo -e "\n== $1 =="; }
have () { command -v "$1" >/dev/null 2>&1; }

# Find repo root (works even if this script lives in a feature subdir)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
  echo "ERROR: Not inside a git repo."
  exit 2
fi

# Directory where this gate lives (feature directory)
GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$GATE_DIR"

python_gate () {
  section "python: tooling check"
  for tool in ruff pyright pytest; do
    if ! have "$tool"; then
      echo "ERROR: '$tool' not found."
      echo "Fix (from this feature dir):"
      echo "  uv venv .venv && source .venv/bin/activate"
      echo "  uv pip install -r requirements-dev.txt"
      exit 2
    fi
  done

  section "repo diff summary"
  git -C "$REPO_ROOT" diff --stat || true
  echo
  git -C "$REPO_ROOT" diff --name-only || true

  section "format (ruff)"
  ruff format .

  section "lint (ruff) - autofix then verify"
  ruff check . --fix
  ruff check .

  section "typecheck (pyright)"
  pyright

  section "tests (pytest)"
  pytest
}

# Expandable dispatcher: add ts_gate(), go_gate(), etc. later
case "$TARGET" in
  python|py) python_gate ;;
  all) python_gate ;;   # later: python_gate; ts_gate; ...
  *)
    echo "Usage: ./scripts/gate.sh [python|all]"
    exit 2
    ;;
esac

section "gate passed"
echo "✅ OK"
