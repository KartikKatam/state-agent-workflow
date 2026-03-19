#!/usr/bin/env bash
# Quality gate — blocking checks that must pass before commit/merge.
#
# Usage:
#   ./scripts/gate.sh                          # Run blocking checks only
#   ./scripts/gate.sh --all                    # Include informational checks
#   ./scripts/gate.sh --agent-id coder-p1-t3   # Auto-annotate agent on failures
#
# When --agent-id is provided, ALL failures (blocking AND informational) are
# written as annotations to ~/.claude/annotations/{agent}.jsonl so the agent
# sees them on its next tool call and can fix them before the next gate run.
#
# Runs from any directory within the repo (finds git root automatically).
# Exit codes: 0 = all blocking checks pass, 1 = one or more failed.
#
# Design doc ref: "Quality Gate" section, lines 445-458.

set -euo pipefail

# ---------------------------------------------------------------------------
# Find repo root
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: Not inside a git repository" >&2
    exit 1
}
cd "$REPO_ROOT"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
RUN_INFORMATIONAL=false
AGENT_ID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --all) RUN_INFORMATIONAL=true; shift ;;
        --agent-id) AGENT_ID="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: gate.sh [--all] [--agent-id AGENT_ID]"
            echo "  --all             Include informational checks (bandit, vulture, pip-audit)"
            echo "  --agent-id ID     Auto-annotate agent with failure details"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

# ---------------------------------------------------------------------------
# Annotation helper
# ---------------------------------------------------------------------------
# Collect all failure messages, then send as a single annotation at the end.
ANNOTATION_MESSAGES=()

annotate_failure() {
    local check_name="$1"
    local blocking="$2"
    local output="$3"

    if [ -z "$AGENT_ID" ]; then
        return
    fi

    local priority="normal"
    if [ "$blocking" = "true" ]; then
        priority="critical"
    fi

    # Truncate output to first 50 lines to keep annotations readable
    local truncated
    truncated=$(echo "$output" | head -50)
    local total_lines
    total_lines=$(echo "$output" | wc -l)
    if [ "$total_lines" -gt 50 ]; then
        truncated="$truncated"$'\n'"... ($((total_lines - 50)) more lines — run gate.sh locally for full output)"
    fi

    ANNOTATION_MESSAGES+=("[$priority] $check_name FAILED:"$'\n'"$truncated")
}

send_annotations() {
    if [ -z "$AGENT_ID" ] || [ ${#ANNOTATION_MESSAGES[@]} -eq 0 ]; then
        return
    fi

    # Combine all failure messages into one annotation
    local combined=""
    for msg in "${ANNOTATION_MESSAGES[@]}"; do
        combined+="$msg"$'\n\n'
    done

    # Determine highest priority (critical if any blocking failure, else normal)
    local priority="normal"
    for msg in "${ANNOTATION_MESSAGES[@]}"; do
        if [[ "$msg" == "[critical]"* ]]; then
            priority="critical"
            break
        fi
    done

    local full_message="Quality gate failures — fix these before next gate run:"$'\n\n'"$combined"

    # Use annotate.sh if available, otherwise write directly
    if [ -x "$SCRIPT_DIR/annotate.sh" ]; then
        "$SCRIPT_DIR/annotate.sh" "$AGENT_ID" "$priority" "$full_message"
    else
        # Direct write fallback
        local annotations_dir="$HOME/.claude/annotations"
        mkdir -p "$annotations_dir"
        local timestamp
        timestamp=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")
        python3 -c "
import json, sys

entry = {
    'timestamp': '$timestamp',
    'priority': '$priority',
    'message': sys.argv[1],
    'source': 'quality_gate',
    'from_agent': None,
    'target_agent': '$AGENT_ID',
    'acknowledged': False,
    'acknowledged_at': None,
    'related_files': None,
    'metadata': {'gate_run': True}
}
print(json.dumps(entry, default=str))
" "$full_message" >> "$annotations_dir/$AGENT_ID.jsonl"
    fi
}

# ---------------------------------------------------------------------------
# Parallel runner
# ---------------------------------------------------------------------------
TMPDIR_GATE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_GATE"' EXIT

FAILURES=0
INFOS=0

run_check_bg() {
    local name="$1"
    local blocking="$2"
    shift 2

    local safe_name
    safe_name=$(echo "$name" | tr ' ' '_')
    local exit_file="$TMPDIR_GATE/${safe_name}.exit"
    local output_file="$TMPDIR_GATE/${safe_name}.out"
    local blocking_file="$TMPDIR_GATE/${safe_name}.blocking"

    echo "$blocking" > "$blocking_file"

    (
        if output=$("$@" 2>&1); then
            echo "0" > "$exit_file"
        else
            echo "1" > "$exit_file"
        fi
        echo "$output" > "$output_file"
    ) &
}

collect_result() {
    local name="$1"

    local safe_name
    safe_name=$(echo "$name" | tr ' ' '_')
    local exit_file="$TMPDIR_GATE/${safe_name}.exit"
    local output_file="$TMPDIR_GATE/${safe_name}.out"
    local blocking_file="$TMPDIR_GATE/${safe_name}.blocking"

    local exit_code
    exit_code=$(cat "$exit_file" 2>/dev/null || echo "1")
    local blocking
    blocking=$(cat "$blocking_file" 2>/dev/null || echo "true")
    local output
    output=$(cat "$output_file" 2>/dev/null || echo "")

    printf "${BOLD}▸ %-20s${RESET} " "$name"

    if [ "$exit_code" = "0" ]; then
        printf "${GREEN}PASS${RESET}\n"
        return 0
    else
        if [ "$blocking" = "true" ]; then
            printf "${RED}FAIL${RESET}\n"
            FAILURES=$((FAILURES + 1))
        else
            printf "${YELLOW}INFO${RESET}\n"
            INFOS=$((INFOS + 1))
        fi
        # Show output on failure (indented)
        if [ -n "$output" ]; then
            echo "$output" | head -30 | sed 's/^/    /'
            local lines
            lines=$(echo "$output" | wc -l)
            if [ "$lines" -gt 30 ]; then
                echo "    ... ($((lines - 30)) more lines)"
            fi
        fi
        # Queue annotation for the agent
        annotate_failure "$name" "$blocking" "$output"
        return 1
    fi
}

# ---------------------------------------------------------------------------
# Blocking checks (parallel)
# ---------------------------------------------------------------------------
echo ""
echo "${BOLD}═══ Quality Gate ═══${RESET}"
echo ""

run_check_bg "ruff format"    true  ruff format . --check
run_check_bg "ruff check"     true  ruff check .
run_check_bg "pyright"        true  pyright
run_check_bg "pytest"         true  pytest --tb=short -q

wait

# Collect in deterministic order
collect_result "ruff format"    || true
collect_result "ruff check"     || true
collect_result "pyright"        || true
collect_result "pytest"         || true

# ---------------------------------------------------------------------------
# Informational checks (optional, parallel)
# ---------------------------------------------------------------------------
if [ "$RUN_INFORMATIONAL" = true ]; then
    echo ""
    echo "${BOLD}─── Informational ───${RESET}"
    echo ""

    INFO_CHECKS=()

    if command -v bandit &>/dev/null; then
        run_check_bg "bandit"     false bandit -r . -q --skip B101
        INFO_CHECKS+=("bandit")
    else
        printf "${BOLD}▸ %-20s${RESET} ${YELLOW}SKIP${RESET} (not installed)\n" "bandit"
    fi

    if command -v vulture &>/dev/null; then
        run_check_bg "vulture"    false vulture . --min-confidence 80
        INFO_CHECKS+=("vulture")
    else
        printf "${BOLD}▸ %-20s${RESET} ${YELLOW}SKIP${RESET} (not installed)\n" "vulture"
    fi

    if command -v pip-audit &>/dev/null; then
        run_check_bg "pip-audit"  false pip-audit
        INFO_CHECKS+=("pip-audit")
    else
        printf "${BOLD}▸ %-20s${RESET} ${YELLOW}SKIP${RESET} (not installed)\n" "pip-audit"
    fi

    if [ ${#INFO_CHECKS[@]} -gt 0 ]; then
        wait
        for check in "${INFO_CHECKS[@]}"; do
            collect_result "$check" || true
        done
    fi
fi

# ---------------------------------------------------------------------------
# Send annotations (all failures in one shot)
# ---------------------------------------------------------------------------
send_annotations

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$FAILURES" -gt 0 ]; then
    echo "${RED}${BOLD}✗ Quality gate FAILED — $FAILURES blocking check(s) failed${RESET}"
    if [ -n "$AGENT_ID" ] && [ ${#ANNOTATION_MESSAGES[@]} -gt 0 ]; then
        echo "  ${YELLOW}Annotation sent to $AGENT_ID with failure details${RESET}"
    fi
    exit 1
else
    echo "${GREEN}${BOLD}✓ Quality gate PASSED${RESET}"
    if [ "$INFOS" -gt 0 ]; then
        echo "  ${YELLOW}($INFOS informational issue(s) — non-blocking)${RESET}"
        if [ -n "$AGENT_ID" ]; then
            echo "  ${YELLOW}Annotation sent to $AGENT_ID — fix before next gate run${RESET}"
        fi
    fi
    exit 0
fi
