#!/bin/bash
# Claude Code Statusline — Almond-Pine themed
# 2-line layout: left columns + right-aligned git block
#
# session    [bar] % used/total    ↑in ↓out                        ~/path
# agent      model  $cost          +add/-rem  duration    @user branch ●s ✎m ?u

input=$(cat)

# --- Terminal width ---
COLS=$(tput cols 2>/dev/null || echo 120)

# --- Almond-Pine Palette ---
PINE="\033[38;2;42;79;62m"       # #2A4F3E — green
PINE_B="\033[38;2;63;122;94m"    # #3F7A5E — bright pine
OCHRE="\033[38;2;168;139;74m"    # #A88B4A — yellow/warning
BRICK="\033[38;2;126;59;46m"     # #7E3B2E — red/danger
TAUPE="\033[38;2;107;93;79m"     # #6B5D4F — muted text
DARK="\033[38;2;74;64;54m"       # #4A4036 — dark brown
LAVENDER="\033[38;2;106;90;122m" # #6A5A7A — accent
TEAL="\033[38;2;46;106;79m"      # #2E6A4F — teal
CREAM="\033[38;2;203;187;168m"   # #CBBBA8 — soft border tone
RST="\033[0m"

# --- Parse JSON (single jq call) ---
eval "$(echo "$input" | jq -r '
  @sh "MODEL_ID=\(.model.id // "-")",
  @sh "USED_PCT_RAW=\(.context_window.used_percentage // 0)",
  @sh "CTX_SIZE=\(.context_window.context_window_size // 200000)",
  @sh "LAST_INPUT=\(.context_window.current_usage.input_tokens // 0)",
  @sh "LAST_OUTPUT=\(.context_window.current_usage.output_tokens // 0)",
  @sh "COST=\(.cost.total_cost_usd // 0)",
  @sh "LINES_ADD=\(.cost.total_lines_added // 0)",
  @sh "LINES_REM=\(.cost.total_lines_removed // 0)",
  @sh "SESSION=\(.session_id // "-")",
  @sh "AGENT=\(.agent.name // "")",
  @sh "PROJECT_DIR=\(.workspace.project_dir // "~")",
  @sh "CURRENT_DIR=\(.workspace.current_dir // "~")"
')"

# --- Derived values ---
MODEL_SHORT=$(echo "$MODEL_ID" | sed 's/^claude-//')
USED_PCT=$(echo "$USED_PCT_RAW" | cut -d. -f1)
SESSION_SHORT=$(echo "$SESSION" | cut -c1-8)
CTX_USED=$(echo "$USED_PCT_RAW * $CTX_SIZE / 100" | bc -l 2>/dev/null | cut -d. -f1)
[ -z "$CTX_USED" ] && CTX_USED=0

# --- Format helpers ---
fmt_tok() {
    local n=$1
    [ -z "$n" ] && n=0
    if [ "$n" -ge 1000000 ]; then
        printf "%.1fM" "$(echo "$n / 1000000" | bc -l)"
    elif [ "$n" -ge 1000 ]; then
        printf "%.1fk" "$(echo "$n / 1000" | bc -l)"
    else
        echo "$n"
    fi
}

CTX_USED_FMT=$(fmt_tok "$CTX_USED")
CTX_SIZE_FMT=$(fmt_tok "$CTX_SIZE")
TOK_UP=$(fmt_tok "$LAST_INPUT")
TOK_DOWN=$(fmt_tok "$LAST_OUTPUT")

COST_FMT=$(printf '$%.2f' "$COST")
PATH_DISPLAY=$(echo "$CURRENT_DIR" | sed "s|^$HOME|~|")

# --- Git info ---
GIT_BRANCH=""
GIT_USER=""
GIT_STAGED=0
GIT_MODIFIED=0
GIT_UNTRACKED=0
if command -v git &>/dev/null && git -C "$CURRENT_DIR" rev-parse --git-dir &>/dev/null; then
    GIT_BRANCH=$(git -C "$CURRENT_DIR" branch --show-current 2>/dev/null)
    GIT_STAGED=$(git -C "$CURRENT_DIR" diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
    GIT_MODIFIED=$(git -C "$CURRENT_DIR" diff --numstat 2>/dev/null | wc -l | tr -d ' ')
    GIT_UNTRACKED=$(git -C "$CURRENT_DIR" ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')
fi
# Cache gh user for 10 minutes to avoid repeated API calls (helps in multi-pane tmux)
GH_CACHE="/tmp/.claude_gh_user_cache"
if [ -f "$GH_CACHE" ] && [ "$(( $(date +%s) - $(stat -c %Y "$GH_CACHE" 2>/dev/null || echo 0) ))" -lt 600 ]; then
    GIT_USER=$(cat "$GH_CACHE")
elif command -v gh &>/dev/null; then
    GIT_USER=$(gh api user --jq '.login' 2>/dev/null)
    [ -n "$GIT_USER" ] && echo "$GIT_USER" > "$GH_CACHE"
fi

# --- Progress bar (responsive width) ---
if [ "$COLS" -ge 200 ]; then
    BAR_WIDTH=20
elif [ "$COLS" -ge 160 ]; then
    BAR_WIDTH=16
elif [ "$COLS" -ge 120 ]; then
    BAR_WIDTH=12
else
    BAR_WIDTH=8
fi

FILLED=$((USED_PCT * BAR_WIDTH / 100))
[ "$FILLED" -gt "$BAR_WIDTH" ] && FILLED=$BAR_WIDTH
EMPTY=$((BAR_WIDTH - FILLED))

if [ "$USED_PCT" -ge 90 ]; then
    BAR_COLOR="$BRICK"
elif [ "$USED_PCT" -ge 70 ]; then
    BAR_COLOR="$OCHRE"
else
    BAR_COLOR="$PINE"
fi

BAR=""
[ "$FILLED" -gt 0 ] && BAR=$(printf "%0.s▓" $(seq 1 "$FILLED"))
[ "$EMPTY" -gt 0 ] && BAR="${BAR}$(printf "%0.s░" $(seq 1 "$EMPTY"))"


# =====================================================================
# COLUMN GRID — 2-line layout with right-aligned git block
# =====================================================================
# EQUI-DISTANT 2-LINE LAYOUT
# =====================================================================
# L1: session      [bar] used/total      ↑in ↓out      ~/path
# L2: agent        model  $cost          +add/-rem dur  @user branch ●s ✎m ?u
#
# Gap = (COLS - total_content_width) / 3, shared equally between 4 blocks

SMALL_GAP="  "

# --- Measure block widths (use widest of L1/L2 per column for alignment) ---

# Block A: session / agent
L1_LABEL="$SESSION_SHORT"
L2_LABEL="${AGENT:--}"
BLK_A_W=${#L1_LABEL}
[ ${#L2_LABEL} -gt "$BLK_A_W" ] && BLK_A_W=${#L2_LABEL}

# Block B: bar+context / model+cost
L1_B_TEXT="[${BAR}] ${CTX_USED_FMT}/${CTX_SIZE_FMT}"
L2_B_TEXT="${MODEL_SHORT}  ${COST_FMT}"
BLK_B_W=${#L1_B_TEXT}
[ ${#L2_B_TEXT} -gt "$BLK_B_W" ] && BLK_B_W=${#L2_B_TEXT}

# Block C: tokens / lines
L1_C_TEXT="↑${TOK_UP} ↓${TOK_DOWN}"
L2_C_TEXT="+${LINES_ADD}/-${LINES_REM}"
BLK_C_W=${#L1_C_TEXT}
[ ${#L2_C_TEXT} -gt "$BLK_C_W" ] && BLK_C_W=${#L2_C_TEXT}

# Block D: path / git info (right block)
R1_TEXT="${PATH_DISPLAY}"
R1_COLORED="${DARK}${PATH_DISPLAY}${RST}"

R2_TEXT=""
R2_COLORED=""
if [ -n "$GIT_USER" ]; then
    R2_TEXT="@${GIT_USER}"
    R2_COLORED="${TAUPE}@${GIT_USER}${RST}"
fi
if [ -n "$GIT_BRANCH" ]; then
    [ -n "$R2_TEXT" ] && R2_TEXT="${R2_TEXT}${SMALL_GAP}" && R2_COLORED="${R2_COLORED}${SMALL_GAP}"
    R2_TEXT="${R2_TEXT}${GIT_BRANCH}"
    R2_COLORED="${R2_COLORED}${PINE_B}${GIT_BRANCH}${RST}"
fi
if [ "$GIT_STAGED" -gt 0 ]; then
    [ -n "$R2_TEXT" ] && R2_TEXT="${R2_TEXT}${SMALL_GAP}" && R2_COLORED="${R2_COLORED}${SMALL_GAP}"
    R2_TEXT="${R2_TEXT}●${GIT_STAGED}"
    R2_COLORED="${R2_COLORED}${PINE}●${GIT_STAGED}${RST}"
fi
if [ "$GIT_MODIFIED" -gt 0 ]; then
    [ -n "$R2_TEXT" ] && R2_TEXT="${R2_TEXT}${SMALL_GAP}" && R2_COLORED="${R2_COLORED}${SMALL_GAP}"
    R2_TEXT="${R2_TEXT}✎${GIT_MODIFIED}"
    R2_COLORED="${R2_COLORED}${OCHRE}✎${GIT_MODIFIED}${RST}"
fi
if [ "$GIT_UNTRACKED" -gt 0 ]; then
    [ -n "$R2_TEXT" ] && R2_TEXT="${R2_TEXT}${SMALL_GAP}" && R2_COLORED="${R2_COLORED}${SMALL_GAP}"
    R2_TEXT="${R2_TEXT}?${GIT_UNTRACKED}"
    R2_COLORED="${R2_COLORED}${LAVENDER}?${GIT_UNTRACKED}${RST}"
fi

BLK_D_W=${#R1_TEXT}
[ ${#R2_TEXT} -gt "$BLK_D_W" ] && BLK_D_W=${#R2_TEXT}

# --- Calculate equi-distant gap ---
TOTAL_CONTENT=$((BLK_A_W + BLK_B_W + BLK_C_W + BLK_D_W))
REMAINING=$((COLS - TOTAL_CONTENT))
GAP=$((REMAINING / 3))
[ "$GAP" -lt 3 ] && GAP=3
GAP_STR=$(printf "%${GAP}s")

# =====================================================================
# LINE 1:  session      [bar] used/total      ↑in ↓out      ~/path
# =====================================================================
L1_A=$(printf "%-${BLK_A_W}s" "$SESSION_SHORT")
L1_A_C="${TAUPE}${L1_A}${RST}"

L1_B=$(printf "%-${BLK_B_W}s" "$L1_B_TEXT")
L1_B_C="${BAR_COLOR}[${BAR}]${RST} ${TAUPE}${CTX_USED_FMT}/${CTX_SIZE_FMT}${RST}"
# Pad block B to BLK_B_W
L1_B_PAD=$((BLK_B_W - ${#L1_B_TEXT}))
[ "$L1_B_PAD" -lt 0 ] && L1_B_PAD=0
L1_B_TRAIL=$(printf "%${L1_B_PAD}s" 2>/dev/null)

L1_C=$(printf "%-${BLK_C_W}s" "$L1_C_TEXT")
L1_C_C="${TAUPE}↑${TOK_UP} ↓${TOK_DOWN}${RST}"
L1_C_PAD=$((BLK_C_W - ${#L1_C_TEXT}))
[ "$L1_C_PAD" -lt 0 ] && L1_C_PAD=0
L1_C_TRAIL=$(printf "%${L1_C_PAD}s" 2>/dev/null)

# Block D right-padded to BLK_D_W
L1_D_C="${R1_COLORED}"

LINE1="${L1_A_C}${GAP_STR}${L1_B_C}${L1_B_TRAIL}${GAP_STR}${L1_C_C}${L1_C_TRAIL}${GAP_STR}${L1_D_C}"

# =====================================================================
# LINE 2:  agent      model  $cost      +add/-rem  dur      @user branch ●s ✎m
# =====================================================================
L2_A=$(printf "%-${BLK_A_W}s" "${AGENT:--}")
L2_A_C="${LAVENDER}${L2_A}${RST}"

# Right-align cost within block B: model on left, cost flush with right edge
L2_B_INNER_PAD=$((BLK_B_W - ${#MODEL_SHORT} - ${#COST_FMT}))
[ "$L2_B_INNER_PAD" -lt 2 ] && L2_B_INNER_PAD=2
L2_B_INNER=$(printf "%${L2_B_INNER_PAD}s")
L2_B_C="${TAUPE}${MODEL_SHORT}${RST}${L2_B_INNER}${TAUPE}${COST_FMT}${RST}"
L2_B_TRAIL=""

L2_C_C="${PINE}+${LINES_ADD}${RST}${DARK}/${RST}${BRICK}-${LINES_REM}${RST}"
L2_C_PAD=$((BLK_C_W - ${#L2_C_TEXT}))
[ "$L2_C_PAD" -lt 0 ] && L2_C_PAD=0
L2_C_TRAIL=$(printf "%${L2_C_PAD}s" 2>/dev/null)

L2_D_C="${R2_COLORED}"

LINE2="${L2_A_C}${GAP_STR}${L2_B_C}${L2_B_TRAIL}${GAP_STR}${L2_C_C}${L2_C_TRAIL}${GAP_STR}${L2_D_C}"

echo -e "$LINE1"
echo -e "$LINE2"

# =====================================================================
# Write context metrics for PostToolUse hook consumption
# =====================================================================
METRICS_DIR="$HOME/.claude/temp"
SESSION_ID="$SESSION"
METRICS_FILE="$METRICS_DIR/context-metrics-${SESSION_ID}.json"
mkdir -p "$METRICS_DIR"

# Total tokens = input + cache_creation + cache_read (all contribute to context window)
INPUT_TOKENS=$(echo "$input" | jq -r '
  .context_window.current_usage |
  ((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0))
')
jq -n \
    --argjson pct "$USED_PCT" \
    --argjson tokens "$INPUT_TOKENS" \
    --argjson max "$CTX_SIZE" \
    --arg ts "$(date -Iseconds)" \
    --arg sid "$SESSION_ID" \
    '{
        used_percentage: $pct,
        input_tokens: $tokens,
        context_window_size: $max,
        session_id: $sid,
        updated_at: $ts
    }' > "$METRICS_FILE"
