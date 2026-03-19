#!/usr/bin/env bash
# inbox_watch.sh — Zero-token idle wake-up for agents.
#
# Usage: inbox_watch.sh <inbox_path> [timeout_seconds]
#
# Blocks until the inbox file is modified, then prints the unread message count.
# Designed to be run via Bash tool with run_in_background: true.
#
# When a message arrives → send_msg.py writes to inbox → inotifywait detects →
# script exits → Claude Code auto-notifies the idle agent.
#
# Requires: inotify-tools (apt-get install inotify-tools)
# Falls back to sleep-based polling if inotifywait is not available.

set -euo pipefail

INBOX_PATH="${1:?Usage: inbox_watch.sh <inbox_path> [timeout_seconds]}"
TIMEOUT="${2:-300}"

# Resolve project root for send_msg.py
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if command -v inotifywait &>/dev/null; then
    # Preferred: event-driven, zero CPU during wait
    inotifywait -t "$TIMEOUT" -e modify -e create "$INBOX_PATH" 2>/dev/null || true
else
    # Fallback: sleep then check (for systems without inotify-tools)
    echo '{"warning": "inotify-tools not installed, using sleep fallback"}'
    sleep "$TIMEOUT"
fi

# Print inbox summary on exit
python3 "$PROJECT_ROOT/scripts/send_msg.py" count 2>/dev/null || echo '{"total": 0, "unread": 0, "blocking_unread": 0}'
