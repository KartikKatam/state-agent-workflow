#!/usr/bin/env bash
# Annotation CLI — inject feedback into an agent's annotation channel.
#
# Usage:
#   ./scripts/annotate.sh <agent> <priority> "<message>"
#   ./scripts/annotate.sh coder-p1-t3-a7f2 critical "Stop — failing test in tracking module"
#   ./scripts/annotate.sh explorer normal "Also check the utils/ directory"
#   ./scripts/annotate.sh researcher fyi "Context7 might have updated docs"
#
# Writes one AnnotationEntry (JSON) per line to ~/.claude/annotations/{agent}.jsonl
# The PostToolUse hook reads unacknowledged entries and injects them via additionalContext.
#
# Priority levels:
#   critical  — agent must stop current work and address immediately
#   normal    — agent integrates into current work
#   fyi       — agent acknowledges, no action required
#
# Design doc ref: "Layer 3: User Annotations" section, lines 271-276.
# Schema ref: schemas/annotation.py — AnnotationEntry

set -euo pipefail

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: annotate.sh <agent> <priority> <message> [options]"
    echo ""
    echo "Arguments:"
    echo "  agent       Agent ID or role name (e.g., coder-p1-t3-a7f2, explorer)"
    echo "  priority    critical | normal | fyi"
    echo "  message     The annotation text (quote it)"
    echo ""
    echo "Options:"
    echo "  --files FILE1,FILE2   Comma-separated related files"
    echo "  --from AGENT_ID       Source agent ID (for orchestrator relay)"
    echo "  -h, --help            Show this help"
    exit "${1:-0}"
}

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
if [ $# -lt 3 ]; then
    usage 1
fi

AGENT="$1"
PRIORITY="$2"
MESSAGE="$3"
shift 3

RELATED_FILES=""
FROM_AGENT=""

while [ $# -gt 0 ]; do
    case "$1" in
        --files)
            RELATED_FILES="$2"
            shift 2
            ;;
        --from)
            FROM_AGENT="$2"
            shift 2
            ;;
        -h|--help)
            usage 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate priority
# ---------------------------------------------------------------------------
case "$PRIORITY" in
    critical|normal|fyi) ;;
    *)
        echo "ERROR: Invalid priority '$PRIORITY'. Must be: critical, normal, fyi" >&2
        exit 1
        ;;
esac

# ---------------------------------------------------------------------------
# Build JSON entry
# ---------------------------------------------------------------------------
ANNOTATIONS_DIR="$HOME/.claude/annotations"
mkdir -p "$ANNOTATIONS_DIR"

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")

# Build related_files array
FILES_JSON="null"
if [ -n "$RELATED_FILES" ]; then
    FILES_JSON=$(echo "$RELATED_FILES" | tr ',' '\n' | python3 -c "
import sys, json
files = [line.strip() for line in sys.stdin if line.strip()]
print(json.dumps(files))
")
fi

# Build from_agent field
FROM_JSON="null"
if [ -n "$FROM_AGENT" ]; then
    FROM_JSON="\"$FROM_AGENT\""
fi

# Use python3 for safe JSON construction (avoids shell quoting issues)
ENTRY=$(python3 -c "
import json, sys

entry = {
    'timestamp': '$TIMESTAMP',
    'priority': '$PRIORITY',
    'message': sys.argv[1],
    'source': 'cli',
    'from_agent': $FROM_JSON if '$FROM_AGENT' else None,
    'target_agent': '$AGENT',
    'acknowledged': False,
    'acknowledged_at': None,
    'related_files': $FILES_JSON if '$RELATED_FILES' else None,
    'metadata': None
}
print(json.dumps(entry, default=str))
" "$MESSAGE")

# ---------------------------------------------------------------------------
# Write to JSONL file
# ---------------------------------------------------------------------------
TARGET_FILE="$ANNOTATIONS_DIR/$AGENT.jsonl"
echo "$ENTRY" >> "$TARGET_FILE"

# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------
case "$PRIORITY" in
    critical) COLOR='\033[0;31m' ;;
    normal)   COLOR='\033[0;33m' ;;
    fyi)      COLOR='\033[0;36m' ;;
esac
RESET='\033[0m'

echo -e "${COLOR}[$PRIORITY]${RESET} Annotation written → $TARGET_FILE"
