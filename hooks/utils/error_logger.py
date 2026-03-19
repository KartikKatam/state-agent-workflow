"""Hook error logging — diagnostic visibility for silenced exceptions.

All hooks swallow exceptions to avoid blocking Claude Code. This module
gives those silenced errors a place to land so bugs don't hide indefinitely.

Log file: ~/.claude/logs/hook-errors.jsonl
Format: one JSON object per line with timestamp, hook, handler, error details.

The log_hook_error function is itself wrapped in try/except — error logging
must never crash the hook it's trying to help.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

_LOG_FILE = Path(os.path.expanduser("~/.claude")) / "logs" / "hook-errors.jsonl"


def log_hook_error(
    hook_name: str,
    handler_name: str,
    error: Exception,
    context: dict | None = None,
) -> None:
    """Append a hook error entry to the JSONL log.

    Never raises — silently fails if logging itself errors.
    """
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "hook": hook_name,
            "handler": handler_name,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exception(error),
            "context": context,
        }
        with _LOG_FILE.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass  # Error logging must never crash
