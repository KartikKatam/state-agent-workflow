#!/usr/bin/env python3
"""
PreToolUse Validation Hook: Schema validation for .claude/ JSON files.

Two validation paths:
1. Write calls — validates JSON content before writing to .claude/**/*.json
2. SendMessage (task_complete) — validates all schema files the agent modified
   during its session, using the session log's files_modified array.
   Blocks completion if any are invalid, so the agent can self-correct.

Hook type: PreToolUse
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path for utils import
sys.path.insert(0, str(Path(__file__).parent))

from utils.schema_validator import match_schema_type, validate_json_file

# Import event logger (optional — Phase 1 may not exist yet)
try:
    from utils.event_logger import emit

    HAS_EVENT_LOGGER = True
except ImportError:
    HAS_EVENT_LOGGER = False


def get_hook_input() -> dict:
    """Read hook input from environment or stdin."""
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        return json.loads(hook_data)

    if not sys.stdin.isatty():
        return json.load(sys.stdin)

    return {}


def _deny(reason: str):
    """Print deny output and exit."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(2)


def _normalize_path(file_path: str) -> str:
    """Normalize to relative path."""
    if os.path.isabs(file_path):
        try:
            return os.path.relpath(file_path)
        except ValueError:
            return file_path
    return file_path


def _is_completion_message(tool_input: dict) -> bool:
    """Detect if this SendMessage is a task_complete message."""
    content = tool_input.get("content", "")

    # Check for task_complete in content (may be JSON or plain text)
    if "task_complete" in content:
        return True

    # Check summary field for completion indicators
    summary = tool_input.get("summary", "").lower()
    completion_keywords = [
        "task complete",
        "work complete",
        "implementation complete",
        "chunk complete",
        "handoff",
        "handing off",
    ]
    if any(kw in summary for kw in completion_keywords):
        return True

    return False


def _find_active_session_log(session_id: str | None = None) -> str | None:
    """
    Find the session log for the current agent.

    Matches on session_id from the hook input against meta.session_id
    in the log. Falls back to most recently modified active log if
    no session_id match is found. Handles both chunk logs and planning logs.
    """
    logs_dir = Path(".claude/logs")
    if not logs_dir.exists():
        return None

    # Search all log files (chunk logs + planning logs)
    log_files = list(logs_dir.glob("*-log.json"))
    if not log_files:
        return None

    log_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # First pass: match on session_id if provided
    if session_id:
        for log_file in log_files:
            try:
                with open(log_file) as f:
                    data = json.load(f)
                if data.get("meta", {}).get("session_id") == session_id:
                    return str(log_file)
            except (json.JSONDecodeError, KeyError):
                pass

    # Fallback: most recently modified active log
    # Includes statuses for both chunk-coder and plan-architect sessions
    _ACTIVE_STATUSES = {
        # chunk-coder statuses
        "in_progress",
        "tests_written",
        "implementation_done",
        "verified",
        # plan-architect statuses
        "structure_proposed",
        "structure_approved",
        "details_written",
        "details_approved",
        "json_saved",
    }
    for log_file in log_files:
        try:
            with open(log_file) as f:
                data = json.load(f)
            if data.get("status") in _ACTIVE_STATUSES:
                return str(log_file)
        except (json.JSONDecodeError, KeyError):
            pass

    return None


def _get_modified_schema_files(session_log_path: str) -> list[str]:
    """
    Extract .claude/ JSON files from the session log's files_modified array
    that match known schema patterns.
    """
    try:
        with open(session_log_path) as f:
            log = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    files_modified = log.get("files_modified", [])
    schema_files = []

    for entry in files_modified:
        path = entry.get("path", "") if isinstance(entry, dict) else str(entry)
        rel = _normalize_path(path)

        if match_schema_type(rel) is not None:
            schema_files.append(rel)

    return schema_files


def validate_write(tool_input: dict):
    """Validate a Write call to .claude/**/*.json."""
    file_path = tool_input.get("file_path", "")
    content = tool_input.get("content", "")

    rel = _normalize_path(file_path)
    if not rel.startswith(".claude/") or not rel.endswith(".json"):
        return

    valid, error = validate_json_file(file_path, content)

    if valid:
        if HAS_EVENT_LOGGER:
            schema_type = match_schema_type(file_path)
            if schema_type:
                emit("validation_passed", file=rel, schema=schema_type)
        return

    if HAS_EVENT_LOGGER:
        schema_type = match_schema_type(file_path) or "unknown"
        emit("validation_failed", file=rel, schema=schema_type, error=error)

    _deny(error)


def _check_log_completeness(session_log_path: str):
    """
    Check content completeness of a session/planning log before allowing
    task_complete. Ensures the log has the required entries — not just
    required fields (which schema validation handles), but meaningful
    content that proves the workflow was followed.

    Denies completion if checks fail; the agent must update the log
    and retry.
    """
    try:
        with open(session_log_path) as f:
            log = json.load(f)
    except (OSError, json.JSONDecodeError):
        return  # Can't read — schema validation will catch it

    rel = _normalize_path(session_log_path)
    schema_type = match_schema_type(session_log_path)

    errors: list[str] = []

    if schema_type == "session-log":
        # --- Chunk-coder log completeness ---

        # phase_history must prove TDD cycle: red_verified + green_verified
        phase_history = log.get("phase_history", [])
        phases_present = {
            entry.get("phase") for entry in phase_history if isinstance(entry, dict)
        }
        for required_phase in ("red_verified", "green_verified"):
            if required_phase not in phases_present:
                errors.append(
                    f"phase_history missing '{required_phase}' entry (required to prove TDD cycle)"
                )

        # tests_written must be non-empty
        if not log.get("tests_written"):
            errors.append("'tests_written' is empty (must list tests created)")

        # files_modified must be non-empty
        if not log.get("files_modified"):
            errors.append("'files_modified' is empty (must list files touched)")

        # quality_gate must show all checks passing
        qg = log.get("quality_gate", {})
        for check in ("format", "lint", "typecheck", "tests"):
            val = str(qg.get(check, "not_run")).lower().split()[0]
            if val not in ("pass", "passed"):
                errors.append(
                    f"quality_gate.{check} is '{qg.get(check, 'not_run')}' (must be pass/passed)"
                )

    elif schema_type == "planning-log":
        # --- Plan-architect log completeness ---

        # source_design_doc must be a non-empty string
        sdd = log.get("source_design_doc", "")
        if not sdd or not str(sdd).strip():
            errors.append(
                "'source_design_doc' is empty (must reference the design doc)"
            )

        # files_created must be non-empty
        if not log.get("files_created"):
            errors.append("'files_created' is empty (must list plan files produced)")

        # phase_history must be non-empty
        if not log.get("phase_history"):
            errors.append("'phase_history' is empty (must record gate approvals)")

    else:
        return  # Unknown log type — skip content checks

    if errors:
        log_label = "session log" if schema_type == "session-log" else "planning log"
        _deny(
            f"Cannot complete: {log_label} at {rel} has"
            f" {len(errors)} completeness issue(s)."
            f" Update the log before sending task_complete.\n"
            + "\n".join(f"  - {e}" for e in errors)
        )

    if HAS_EVENT_LOGGER:
        emit(
            "log_completeness_passed",
            file=rel,
            schema=schema_type,
        )


def validate_completion(tool_input: dict, session_id: str | None = None):
    """
    Validate all schema files the agent modified before allowing completion.

    Two-phase validation:
    1. Content completeness — the session/planning log has required entries
    2. Schema validation — all modified .claude/ JSON files have required fields
    """
    session_log_path = _find_active_session_log(session_id)
    if not session_log_path:
        return  # No session log — allow through

    # Phase 1: Check log content completeness
    _check_log_completeness(session_log_path)

    # Phase 2: Validate schema files the agent modified
    schema_files = _get_modified_schema_files(session_log_path)
    if not schema_files:
        return  # No schema files modified — allow through

    errors = []

    for rel in schema_files:
        # Resolve to absolute path for reading from disk
        abs_path = Path(rel).resolve()
        if not abs_path.exists():
            continue  # File was deleted — skip

        try:
            content = abs_path.read_text()
        except OSError:
            continue

        valid, error = validate_json_file(rel, content)
        if not valid:
            errors.append(error)
            if HAS_EVENT_LOGGER:
                schema_type = match_schema_type(rel) or "unknown"
                emit(
                    "validation_failed",
                    file=rel,
                    schema=schema_type,
                    error=error,
                    trigger="completion_gate",
                )

    if errors:
        reason = (
            f"Cannot complete: {len(errors)} schema file(s) have validation errors. "
            f"Fix them before sending completion.\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
        _deny(reason)

    # All valid
    if HAS_EVENT_LOGGER:
        emit(
            "completion_validation_passed",
            files_checked=len(schema_files),
            session_log=session_log_path,
        )


def main():
    """Main hook handler."""
    hook_input = get_hook_input()

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    session_id = hook_input.get("session_id")

    if tool_name == "Write":
        validate_write(tool_input)
    elif tool_name == "SendMessage" and _is_completion_message(tool_input):
        validate_completion(tool_input, session_id)


if __name__ == "__main__":
    main()
