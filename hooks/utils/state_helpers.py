#!/usr/bin/env python3
"""
State Helpers Utility

Common state-related operations used across all hook utilities.
Reads agent identity and state from the environment and state files.

Agent state files: ~/.claude/state/agents/{agent-id}.json
"""

import fcntl
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

# ---------------------------------------------------------------------------
# Directory layout (all under ~/.claude/)
# ---------------------------------------------------------------------------

_CLAUDE_HOME = Path(os.path.expanduser("~/.claude"))

AGENT_STATE_DIR = _CLAUDE_HOME / "state" / "agents"
LOGS_DIR = _CLAUDE_HOME / "logs"
DECISIONS_LOG_DIR = LOGS_DIR / "decisions"
SNAPSHOTS_DIR = LOGS_DIR / "snapshots"
ANNOTATIONS_DIR = _CLAUDE_HOME / "annotations"
PORTS_DIR = _CLAUDE_HOME / "state" / "ports"
TEMP_DIR = _CLAUDE_HOME / "temp"

_REQUIRED_DIRS = [
    AGENT_STATE_DIR,
    LOGS_DIR,
    DECISIONS_LOG_DIR,
    SNAPSHOTS_DIR,
    ANNOTATIONS_DIR,
    PORTS_DIR,
    TEMP_DIR,
]


def get_agent_id() -> str:
    """
    Read the current agent's ID from the environment.

    Returns the CLAUDE_CODE_AGENT_NAME env var, or "unknown" if unset.
    Agents spawned by the orchestrator have this set at dispatch time.
    """
    return os.environ.get("CLAUDE_CODE_AGENT_NAME", "unknown")


def _agent_state_path(agent_id: str) -> Path:
    """Return the path to an agent's state file."""
    return AGENT_STATE_DIR / f"{agent_id}.json"


def read_json_safe(path: str | Path) -> dict | None:
    """
    Safely read and parse a JSON file.

    Returns the parsed dict, or None if the file doesn't exist,
    can't be read, or contains invalid JSON. Never raises.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def get_agent_role(agent_id: str) -> str:
    """
    Read an agent's role from its state file.

    Returns the role string (e.g. "coder", "explorer"), or "unknown"
    if the state file doesn't exist or has no role field.
    """
    state = read_json_safe(_agent_state_path(agent_id))
    if state:
        return state.get("role", "unknown")
    return "unknown"


def get_current_state(agent_id: str) -> str:
    """
    Read an agent's current state machine state from its state file.

    Returns the state string (e.g. "IMPLEMENTATION", "SYNTHESIS"),
    or "UNKNOWN" if the state file doesn't exist or has no current_state field.
    """
    state = read_json_safe(_agent_state_path(agent_id))
    if state:
        return state.get("current_state", "UNKNOWN")
    return "UNKNOWN"


def read_stdin() -> dict:
    """Read and parse JSON from stdin or CLAUDE_HOOK_DATA env var.

    Checks CLAUDE_HOOK_DATA first (used by some hook runners),
    then falls back to reading stdin. Returns empty dict on failure.
    Never raises.
    """
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        try:
            data = json.loads(hook_data)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
        return {}
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read().strip()
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def atomic_write(path: str | Path, content: str) -> None:
    """Write content to path atomically via temp file + rename.

    Creates a temp file in the same directory, writes content,
    then atomically replaces the target. Cleans up on failure.
    """
    path = Path(path)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def locked_read_modify_write(
    path: str | Path, modifier: Callable[[dict], dict]
) -> bool:
    """Atomically read, modify, and write a JSON file with advisory locking.

    Opens the file with an exclusive lock, reads JSON, applies the modifier
    function, and writes back. The lock is held for the entire operation,
    preventing race conditions between concurrent hooks/agents.

    Returns True on success, False on any error. Never raises.
    """
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                data = json.load(f)
                modified = modifier(data)
                f.seek(0)
                f.truncate()
                json.dump(modified, f, indent=2, default=str)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception:
        return False


def locked_read_modify_write_jsonl(
    path: str | Path, modifier: Callable[[list[dict]], list[dict]]
) -> bool:
    """Atomically read, modify, and write a JSONL file with advisory locking.

    Like locked_read_modify_write but for JSONL files (one JSON object per line).
    Returns True on success, False on any error. Never raises.
    """
    try:
        with open(path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                entries: list[dict] = []
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                modified = modifier(entries)
                f.seek(0)
                f.truncate()
                for entry in modified:
                    f.write(json.dumps(entry, default=str) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return True
    except Exception:
        return False


def ensure_dirs() -> None:
    """
    Create all required directories for the hook/state system.

    Safe to call multiple times — uses exist_ok=True.
    Called by SessionStart hook at agent registration.
    """
    for d in _REQUIRED_DIRS:
        d.mkdir(parents=True, exist_ok=True)
