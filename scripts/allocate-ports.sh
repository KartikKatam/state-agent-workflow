#!/usr/bin/env bash
# Port allocation for parallel agents — prevents port conflicts when
# multiple coders run dev servers simultaneously.
#
# Usage:
#   ./scripts/allocate-ports.sh allocate <agent-id> [--count N]
#   ./scripts/allocate-ports.sh release <agent-id>
#   ./scripts/allocate-ports.sh list
#   ./scripts/allocate-ports.sh cleanup
#   ./scripts/allocate-ports.sh query <agent-id>
#
# Port range: 30000-39999 (design doc ref: "Worktree Strategy", lines 465-476)
# State files: ~/.claude/state/ports/{agent-id}.json
#
# Each allocation reserves a contiguous block of N ports (default 10).
# Before allocating, checks ss -tlnp for actual port conflicts.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PORT_MIN=30000
PORT_MAX=39999
DEFAULT_COUNT=10
STATE_DIR="$HOME/.claude/state/ports"

mkdir -p "$STATE_DIR"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: allocate-ports.sh <command> [args]"
    echo ""
    echo "Commands:"
    echo "  allocate <agent-id> [--count N]  Allocate N ports (default $DEFAULT_COUNT)"
    echo "  release <agent-id>               Release allocated ports"
    echo "  list                             Show all allocations"
    echo "  cleanup                          Remove stale allocations (dead agents)"
    echo "  query <agent-id>                 Show ports for a specific agent"
    echo ""
    echo "Port range: $PORT_MIN-$PORT_MAX"
    exit "${1:-0}"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Get all ports currently in use on the system
get_used_ports() {
    ss -tlnp 2>/dev/null | awk 'NR>1 {
        split($4, a, ":");
        port = a[length(a)];
        if (port+0 >= '"$PORT_MIN"' && port+0 <= '"$PORT_MAX"')
            print port
    }' | sort -un
}

# Get all ports currently allocated to any agent
get_allocated_ports() {
    for f in "$STATE_DIR"/*.json; do
        [ -f "$f" ] || continue
        python3 -c "
import json, sys
with open('$f') as fh:
    data = json.load(fh)
for p in data.get('ports', []):
    print(p)
" 2>/dev/null
    done | sort -un
}

# Find a contiguous block of N free ports
find_free_block() {
    local count="$1"

    # Collect all unavailable ports (allocated + system in-use)
    local unavailable
    unavailable=$(
        { get_allocated_ports; get_used_ports; } | sort -un
    )

    # Scan for contiguous block
    local start=$PORT_MIN
    while [ $start -le $((PORT_MAX - count + 1)) ]; do
        local conflict=false
        for offset in $(seq 0 $((count - 1))); do
            local port=$((start + offset))
            if echo "$unavailable" | grep -qx "$port"; then
                conflict=true
                start=$((port + 1))
                break
            fi
        done
        if [ "$conflict" = false ]; then
            # Return the block
            for offset in $(seq 0 $((count - 1))); do
                echo $((start + offset))
            done
            return 0
        fi
    done

    echo "ERROR: Cannot find $count contiguous free ports in range $PORT_MIN-$PORT_MAX" >&2
    return 1
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_allocate() {
    local agent_id="$1"
    local count="$DEFAULT_COUNT"
    shift

    while [ $# -gt 0 ]; do
        case "$1" in
            --count) count="$2"; shift 2 ;;
            *) echo "Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    # Check for existing allocation
    local state_file="$STATE_DIR/$agent_id.json"
    if [ -f "$state_file" ]; then
        echo "ERROR: Agent '$agent_id' already has ports allocated. Release first." >&2
        exit 1
    fi

    # Find free ports
    local ports
    ports=$(find_free_block "$count") || exit 1

    # Build port list as JSON array
    local ports_json
    ports_json=$(echo "$ports" | python3 -c "
import sys, json
ports = [int(line.strip()) for line in sys.stdin if line.strip()]
print(json.dumps(ports))
")

    local first_port last_port
    first_port=$(echo "$ports" | head -1)
    last_port=$(echo "$ports" | tail -1)

    # Write state file
    python3 -c "
import json
from datetime import datetime, timezone

data = {
    'agent_id': '$agent_id',
    'ports': $ports_json,
    'port_range': {'start': $first_port, 'end': $last_port},
    'count': $count,
    'allocated_at': datetime.now(timezone.utc).isoformat()
}
with open('$state_file', 'w') as f:
    json.dump(data, f, indent=2)
"

    echo "Allocated $count ports for $agent_id: $first_port-$last_port"

    # Output JSON for programmatic use
    if [ -t 1 ]; then
        # Interactive — just the summary above
        :
    else
        # Piped — output JSON
        cat "$state_file"
    fi
}

cmd_release() {
    local agent_id="$1"
    local state_file="$STATE_DIR/$agent_id.json"

    if [ ! -f "$state_file" ]; then
        echo "No allocation found for agent '$agent_id'" >&2
        exit 1
    fi

    rm "$state_file"
    echo "Released ports for $agent_id"
}

cmd_list() {
    local found=false
    printf "%-35s %-15s %-10s %s\n" "AGENT" "RANGE" "COUNT" "ALLOCATED"
    printf "%-35s %-15s %-10s %s\n" "─────" "─────" "─────" "─────────"

    for f in "$STATE_DIR"/*.json; do
        [ -f "$f" ] || continue
        found=true
        python3 -c "
import json
with open('$f') as fh:
    d = json.load(fh)
r = d.get('port_range', {})
print(f\"{d['agent_id']:<35} {r.get('start','?')}-{r.get('end','?'):<10} {d.get('count','?'):<10} {d.get('allocated_at','?')[:19]}\")
" 2>/dev/null
    done

    if [ "$found" = false ]; then
        echo "(no allocations)"
    fi
}

cmd_query() {
    local agent_id="$1"
    local state_file="$STATE_DIR/$agent_id.json"

    if [ ! -f "$state_file" ]; then
        echo "No allocation found for agent '$agent_id'" >&2
        exit 1
    fi

    cat "$state_file"
}

cmd_cleanup() {
    local cleaned=0
    local agents_dir="$HOME/.claude/state/agents"

    for f in "$STATE_DIR"/*.json; do
        [ -f "$f" ] || continue
        local agent_id
        agent_id=$(python3 -c "import json; print(json.load(open('$f'))['agent_id'])" 2>/dev/null) || continue

        # Check if agent state exists and is still active
        local agent_file="$agents_dir/$agent_id.json"
        local stale=false

        if [ ! -f "$agent_file" ]; then
            stale=true
        else
            local status
            status=$(python3 -c "import json; print(json.load(open('$agent_file')).get('status', 'unknown'))" 2>/dev/null)
            if [ "$status" = "terminated" ]; then
                stale=true
            fi
        fi

        if [ "$stale" = true ]; then
            rm "$f"
            echo "Cleaned up stale allocation: $agent_id"
            cleaned=$((cleaned + 1))
        fi
    done

    echo "Cleanup complete: $cleaned stale allocation(s) removed"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
if [ $# -lt 1 ]; then
    usage 1
fi

COMMAND="$1"
shift

case "$COMMAND" in
    allocate)
        [ $# -lt 1 ] && { echo "ERROR: allocate requires <agent-id>" >&2; exit 1; }
        cmd_allocate "$@"
        ;;
    release)
        [ $# -lt 1 ] && { echo "ERROR: release requires <agent-id>" >&2; exit 1; }
        cmd_release "$1"
        ;;
    list)
        cmd_list
        ;;
    query)
        [ $# -lt 1 ] && { echo "ERROR: query requires <agent-id>" >&2; exit 1; }
        cmd_query "$1"
        ;;
    cleanup)
        cmd_cleanup
        ;;
    -h|--help)
        usage 0
        ;;
    *)
        echo "Unknown command: $COMMAND" >&2
        usage 1
        ;;
esac
