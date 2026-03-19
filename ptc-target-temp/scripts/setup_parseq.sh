#!/usr/bin/env bash
# One-time PARSeq local installation: clone, install, download weights.
# Idempotent — safe to re-run.
#
# Usage: bash scripts/setup_parseq.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

PARSEQ_TAG="v1.0.0"
PARSEQ_REPO="https://github.com/baudm/parseq.git"
VENDOR_DIR="${PROJECT_ROOT}/vendor/parseq"
MODELS_DIR="${PROJECT_ROOT}/models"
WEIGHTS_FILE="${MODELS_DIR}/parseq.pt"
WEIGHTS_URL="https://github.com/baudm/parseq/releases/download/${PARSEQ_TAG}/parseq-bb5792a6.pt"
# SHA256 prefix from PyTorch hub filename convention (8 hex chars = 32-bit integrity).
# PyTorch hub uses this same prefix verification internally.
WEIGHTS_SHA256_PREFIX="bb5792a6"

echo "=== PARSeq Setup ==="
echo ""

# --- Step 1: Clone PARSeq at pinned tag ---
if [ -d "${VENDOR_DIR}" ]; then
    echo "[1/5] vendor/parseq exists — verifying tag..."
    CURRENT_TAG=$(cd "${VENDOR_DIR}" && git describe --tags --exact-match HEAD 2>/dev/null || echo "none")
    if [ "${CURRENT_TAG}" != "${PARSEQ_TAG}" ]; then
        echo "ERROR: vendor/parseq at tag '${CURRENT_TAG}', expected '${PARSEQ_TAG}'"
        echo "  Fix: rm -rf ${VENDOR_DIR} && re-run this script"
        exit 1
    fi
    echo "  Tag verified: ${PARSEQ_TAG}"
else
    echo "[1/5] Cloning baudm/parseq at ${PARSEQ_TAG}..."
    mkdir -p "$(dirname "${VENDOR_DIR}")"
    git clone --branch "${PARSEQ_TAG}" --depth 1 "${PARSEQ_REPO}" "${VENDOR_DIR}"
    echo "  Cloned to ${VENDOR_DIR}"
fi

# --- Step 2: Editable install (no-deps) ---
# H1, H3: editable install mandatory — strhub uses relative config paths.
# We install --no-deps because PARSeq v1.0.0 pins torch~=1.10.2 which is
# incompatible with Python 3.12+.  The project venv already has torch, timm,
# pytorch-lightning, and nltk installed at compatible versions.
echo ""
echo "[2/5] Installing PARSeq (editable, no-deps)..."
if command -v uv &>/dev/null; then
    uv pip install -e "${VENDOR_DIR}" --no-deps --python "${PROJECT_ROOT}/.venv/bin/python"
else
    pip install -e "${VENDOR_DIR}" --no-deps
fi

# --- Step 3: Verify editable install ---
echo ""
echo "[3/5] Verifying install..."
python -c "from strhub.models.utils import init_weights; print('  strhub importable: OK')"
if [ -f "${VENDOR_DIR}/configs/main.yaml" ]; then
    echo "  configs/main.yaml accessible: OK"
else
    echo "  WARNING: configs/main.yaml not found in vendor/parseq"
fi

# --- Step 4: Download pretrained weights ---
echo ""
mkdir -p "${MODELS_DIR}"
if [ -f "${WEIGHTS_FILE}" ]; then
    echo "[4/5] Weights already at ${WEIGHTS_FILE}"
else
    echo "[4/5] Downloading pretrained weights..."
    curl -fL -o "${WEIGHTS_FILE}" "${WEIGHTS_URL}"
    echo "  Downloaded to ${WEIGHTS_FILE}"
fi

# --- Step 5: Verify SHA256 ---
echo ""
echo "[5/5] Verifying weights integrity..."
ACTUAL_SHA256=$(sha256sum "${WEIGHTS_FILE}" | cut -d' ' -f1)
if [[ "${ACTUAL_SHA256}" == ${WEIGHTS_SHA256_PREFIX}* ]]; then
    echo "  SHA256 prefix verified: ${WEIGHTS_SHA256_PREFIX}"
    echo "  Full SHA256: ${ACTUAL_SHA256}"
else
    echo "ERROR: SHA256 mismatch!"
    echo "  Expected prefix: ${WEIGHTS_SHA256_PREFIX}"
    echo "  Actual:          ${ACTUAL_SHA256}"
    echo "  File may be corrupted. Removing and aborting."
    rm -f "${WEIGHTS_FILE}"
    exit 1
fi

echo ""
echo "=== Setup complete ==="
echo "  PARSeq:  ${VENDOR_DIR} (${PARSEQ_TAG})"
echo "  Weights: ${WEIGHTS_FILE}"
echo ""
echo "Next step:"
echo "  python scripts/export_parseq_trt.py --checkpoint ${WEIGHTS_FILE}"
