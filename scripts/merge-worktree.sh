#!/usr/bin/env bash
# Merge agent worktree back to integration branch.
#
# Usage:
#   ./scripts/merge-worktree.sh <worktree-path> <base-branch> [options]
#   ./scripts/merge-worktree.sh /tmp/wt-coder-p1-t3 feature/lpr-tracking
#   ./scripts/merge-worktree.sh /tmp/wt-coder-p1-t3 feature/lpr-tracking --no-squash
#   ./scripts/merge-worktree.sh /tmp/wt-coder-p1-t3 feature/lpr-tracking --skip-gate
#
# Flow:
#   1. Create backup ref (refs/backups/{branch}-{timestamp})
#   2. Run quality gate in worktree (unless --skip-gate)
#   3. Squash-merge worktree branch into base branch (or regular merge with --no-squash)
#   4. On success: clean up worktree + branch
#   5. On failure: preserve worktree, print diagnostics
#
# Design doc ref: "Worktree Strategy" section, lines 465-476.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GATE_SCRIPT="$SCRIPT_DIR/gate.sh"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: merge-worktree.sh <worktree-path> <base-branch> [options]"
    echo ""
    echo "Arguments:"
    echo "  worktree-path   Path to the agent's worktree"
    echo "  base-branch     Branch to merge into (e.g., feature/lpr-tracking)"
    echo ""
    echo "Options:"
    echo "  --no-squash     Use regular merge instead of squash merge"
    echo "  --skip-gate     Skip quality gate (use only for emergency merges)"
    echo "  --message MSG   Custom commit message (default: auto-generated)"
    echo "  --agent-id ID   Agent ID for commit message attribution"
    echo "  -h, --help      Show this help"
    exit "${1:-0}"
}

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

log_step() { echo -e "${BOLD}▸${RESET} $1"; }
log_ok()   { echo -e "  ${GREEN}✓${RESET} $1"; }
log_warn() { echo -e "  ${YELLOW}⚠${RESET} $1"; }
log_err()  { echo -e "  ${RED}✗${RESET} $1"; }

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
if [ $# -lt 2 ]; then
    usage 1
fi

WORKTREE_PATH="$1"
BASE_BRANCH="$2"
shift 2

SQUASH=true
SKIP_GATE=false
COMMIT_MSG=""
AGENT_ID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --no-squash) SQUASH=false; shift ;;
        --skip-gate) SKIP_GATE=true; shift ;;
        --message)   COMMIT_MSG="$2"; shift 2 ;;
        --agent-id)  AGENT_ID="$2"; shift 2 ;;
        -h|--help)   usage 0 ;;
        *)           echo "Unknown option: $1" >&2; usage 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------
if [ ! -d "$WORKTREE_PATH" ]; then
    echo "ERROR: Worktree path does not exist: $WORKTREE_PATH" >&2
    exit 1
fi

if [ ! -d "$WORKTREE_PATH/.git" ] && [ ! -f "$WORKTREE_PATH/.git" ]; then
    echo "ERROR: Not a git worktree: $WORKTREE_PATH" >&2
    exit 1
fi

# Get the main repo root (where worktree was created from)
MAIN_REPO=$(git -C "$WORKTREE_PATH" rev-parse --git-common-dir)
MAIN_REPO=$(cd "$MAIN_REPO" && cd .. && pwd)

# Get worktree branch name
WT_BRANCH=$(git -C "$WORKTREE_PATH" rev-parse --abbrev-ref HEAD)

# Verify base branch exists
if ! git -C "$MAIN_REPO" rev-parse --verify "$BASE_BRANCH" &>/dev/null; then
    echo "ERROR: Base branch '$BASE_BRANCH' does not exist" >&2
    exit 1
fi

echo ""
echo "${BOLD}═══ Merge Worktree ═══${RESET}"
echo "  Worktree:  $WORKTREE_PATH"
echo "  Branch:    $WT_BRANCH"
echo "  Target:    $BASE_BRANCH"
echo "  Mode:      $([ "$SQUASH" = true ] && echo "squash" || echo "regular")"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Create backup ref
# ---------------------------------------------------------------------------
log_step "Creating backup ref..."

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_REF="refs/backups/${BASE_BRANCH//\//-}-$TIMESTAMP"
BASE_SHA=$(git -C "$MAIN_REPO" rev-parse "$BASE_BRANCH")
git -C "$MAIN_REPO" update-ref "$BACKUP_REF" "$BASE_SHA"
log_ok "Backup: $BACKUP_REF ($BASE_SHA)"

# ---------------------------------------------------------------------------
# Step 2: Run quality gate in worktree
# ---------------------------------------------------------------------------
if [ "$SKIP_GATE" = false ]; then
    log_step "Running quality gate in worktree..."
    if (cd "$WORKTREE_PATH" && bash "$GATE_SCRIPT"); then
        log_ok "Quality gate passed"
    else
        log_err "Quality gate FAILED — aborting merge"
        echo ""
        echo "Worktree preserved at: $WORKTREE_PATH"
        echo "Fix issues and re-run, or use --skip-gate to bypass"
        exit 1
    fi
else
    log_warn "Quality gate skipped (--skip-gate)"
fi

# ---------------------------------------------------------------------------
# Step 3: Ensure worktree changes are committed
# ---------------------------------------------------------------------------
log_step "Checking worktree status..."

if [ -n "$(git -C "$WORKTREE_PATH" status --porcelain)" ]; then
    log_err "Worktree has uncommitted changes — aborting"
    echo "  Commit or stash changes in $WORKTREE_PATH before merging"
    exit 1
fi
log_ok "Worktree clean"

# ---------------------------------------------------------------------------
# Step 4: Merge
# ---------------------------------------------------------------------------
log_step "Merging into $BASE_BRANCH..."

# Build commit message
if [ -z "$COMMIT_MSG" ]; then
    COMMIT_MSG="Merge $WT_BRANCH into $BASE_BRANCH"
    if [ -n "$AGENT_ID" ]; then
        COMMIT_MSG="[$AGENT_ID] $COMMIT_MSG"
    fi
    # Add commit summary from worktree
    MERGE_BASE=$(git -C "$MAIN_REPO" merge-base "$BASE_BRANCH" "$WT_BRANCH" 2>/dev/null || echo "")
    if [ -n "$MERGE_BASE" ]; then
        COMMIT_LOG=$(git -C "$WORKTREE_PATH" log --oneline "$MERGE_BASE..$WT_BRANCH" 2>/dev/null | head -20)
        if [ -n "$COMMIT_LOG" ]; then
            COMMIT_MSG="$COMMIT_MSG"$'\n\n'"Commits:"$'\n'"$COMMIT_LOG"
        fi
    fi
fi

# Perform merge in main repo
cd "$MAIN_REPO"

# Checkout base branch
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
git checkout "$BASE_BRANCH" --quiet

merge_cleanup() {
    # Restore original branch on failure
    if [ -n "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "HEAD" ]; then
        git checkout "$ORIGINAL_BRANCH" --quiet 2>/dev/null || true
    fi
}

if [ "$SQUASH" = true ]; then
    if git merge --squash "$WT_BRANCH" --quiet 2>/dev/null; then
        git commit -m "$COMMIT_MSG" --quiet
        log_ok "Squash-merged successfully"
    else
        log_err "Merge conflict during squash merge"
        echo ""
        echo "  Conflict resolution options:"
        echo "  1. Resolve conflicts in $MAIN_REPO, then: git commit"
        echo "  2. Abort: git merge --abort"
        echo "  3. Restore from backup: git reset --hard $BACKUP_REF"
        echo ""
        echo "  Worktree preserved at: $WORKTREE_PATH"
        echo "  git rerere may have recorded prior resolutions"
        merge_cleanup
        exit 1
    fi
else
    if git merge "$WT_BRANCH" -m "$COMMIT_MSG" --quiet 2>/dev/null; then
        log_ok "Merged successfully"
    else
        log_err "Merge conflict"
        echo ""
        echo "  Conflict resolution options:"
        echo "  1. Resolve conflicts in $MAIN_REPO, then: git commit"
        echo "  2. Abort: git merge --abort"
        echo "  3. Restore from backup: git reset --hard $BACKUP_REF"
        echo ""
        echo "  Worktree preserved at: $WORKTREE_PATH"
        echo "  git rerere may have recorded prior resolutions"
        merge_cleanup
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Step 5: Cleanup
# ---------------------------------------------------------------------------
log_step "Cleaning up worktree..."

git worktree remove "$WORKTREE_PATH" --force 2>/dev/null && \
    log_ok "Worktree removed" || \
    log_warn "Could not remove worktree (remove manually: git worktree remove $WORKTREE_PATH)"

# Delete worktree branch
git branch -D "$WT_BRANCH" --quiet 2>/dev/null && \
    log_ok "Branch $WT_BRANCH deleted" || \
    log_warn "Could not delete branch $WT_BRANCH"

# Restore original branch if different from base
if [ -n "$ORIGINAL_BRANCH" ] && [ "$ORIGINAL_BRANCH" != "HEAD" ] && [ "$ORIGINAL_BRANCH" != "$BASE_BRANCH" ]; then
    git checkout "$ORIGINAL_BRANCH" --quiet 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
NEW_SHA=$(git rev-parse "$BASE_BRANCH")
echo "${GREEN}${BOLD}✓ Merge complete${RESET}"
echo "  $BASE_BRANCH: $BASE_SHA → $NEW_SHA"
echo "  Backup ref: $BACKUP_REF"

# Release ports if agent-id provided
if [ -n "$AGENT_ID" ]; then
    PORTS_FILE="$HOME/.claude/state/ports/$AGENT_ID.json"
    if [ -f "$PORTS_FILE" ]; then
        rm "$PORTS_FILE"
        log_ok "Released port allocation for $AGENT_ID"
    fi
fi
