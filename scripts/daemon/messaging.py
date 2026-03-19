"""Daemon messaging module -- handles message notifications and inbox blocking.

Manages the connection between the file-based messaging system and the
daemon's annotation/blocking infrastructure.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from hooks.utils.event_logger import emit
from hooks.utils.state_helpers import (
    ANNOTATIONS_DIR,
    locked_read_modify_write,
)
from scripts.daemon.state_manager import (
    AGENTS_DIR,
    _invalidate_agent_cache,
)

_log = logging.getLogger("workflow_state")

# In-memory escalation tracking (intentionally not persisted -- annotations are the durability layer)
_pending_escalations: dict[
    str, list[dict]
] = {}  # agent_id -> [{message_id, escalate_on_states}]

# Ready states that trigger non-blocking escalation
ESCALATION_STATES: set[str] = {
    "TASK_COMPLETE",
    "IDLE",
    "SYNTHESIS",
    "WRITING",
    "REVIEW_COMPLETE",
    "SPAWNED",
}


def handle_message_notification(
    from_agent: str,
    to_agent: str,
    team_id: str,  # noqa: ARG001 — kept for API consistency
    message_id: str,
    priority: str,
    message_type: str,
) -> dict:
    """Handle a new message notification from send_msg.py.

    1. Write annotation to ~/.claude/annotations/{to_agent}.jsonl
    2. If blocking: set inbox_blocked on agent state
    3. If non-blocking: register in _pending_escalations
    4. Emit observability event
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Write annotation
    ann_priority = "critical" if priority == "blocking" else "normal"
    annotation = {
        "priority": ann_priority,
        "message": (
            f"New {message_type} message from {from_agent} (priority={priority}). "
            f"Read inbox via PTC, then ack: send-msg ack --message-id {message_id}"
        ),
        "from_agent": from_agent,
        "annotation_id": f"msg-{message_id[:8]}",
        "acknowledged": False,
        "timestamp": now_iso,
    }

    ann_file = ANNOTATIONS_DIR / f"{to_agent}.jsonl"
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with ann_file.open("a") as f:
            f.write(json.dumps(annotation, default=str) + "\n")
    except Exception as e:
        _log.warning("Failed to write annotation for %s: %s", to_agent, e)

    # 2. If blocking: set inbox_blocked on agent state
    if priority == "blocking":
        agent_state_path = AGENTS_DIR / f"{to_agent}.json"
        if agent_state_path.exists():

            def _set_blocked(data: dict) -> dict:
                data["inbox_blocked"] = {
                    "blocked_by_message_id": message_id,
                    "from_agent": from_agent,
                }
                return data

            locked_read_modify_write(agent_state_path, _set_blocked)
            _invalidate_agent_cache(to_agent)

    # 3. If non-blocking: register for escalation
    if priority == "non-blocking":
        _pending_escalations.setdefault(to_agent, []).append(
            {
                "message_id": message_id,
                "from_agent": from_agent,
                "message_type": message_type,
            }
        )

    # 4. Emit event
    emit(
        "message_delivered",
        from_agent=from_agent,
        to_agent=to_agent,
        message_type=message_type,
        message_id=message_id,
        priority=priority,
    )

    return {"ok": True, "annotation_written": True, "blocked": priority == "blocking"}


def handle_message_ack(agent_id: str, message_id: str, team_id: str) -> dict:  # noqa: ARG001
    """Handle a message acknowledgment.

    1. Clear inbox_blocked if blocked_by_message_id matches
    2. Remove from _pending_escalations
    3. Emit observability event
    """
    # 1. Clear inbox_blocked if it matches
    agent_state_path = AGENTS_DIR / f"{agent_id}.json"
    if agent_state_path.exists():

        def _clear_blocked(data: dict) -> dict:
            blocked = data.get("inbox_blocked")
            if blocked and blocked.get("blocked_by_message_id") == message_id:
                data["inbox_blocked"] = None
            return data

        locked_read_modify_write(agent_state_path, _clear_blocked)
        _invalidate_agent_cache(agent_id)

    # 2. Remove from escalations
    escalations = _pending_escalations.get(agent_id, [])
    _pending_escalations[agent_id] = [
        e for e in escalations if e["message_id"] != message_id
    ]

    # 3. Emit event
    emit("message_acked", agent=agent_id, message_id=message_id)

    return {"ok": True}


def check_inbox_block(agent_id: str) -> dict | None:
    """Read agent state, return inbox_blocked dict or None.

    Called by PreToolUse to check if agent is blocked.
    """
    agent_state_path = AGENTS_DIR / f"{agent_id}.json"
    if not agent_state_path.exists():
        return None
    try:
        data = json.loads(agent_state_path.read_text())
        return data.get("inbox_blocked")
    except Exception:
        return None


def check_escalation(agent_id: str, new_state: str) -> list[str]:
    """Check if pending non-blocking messages should escalate.

    Called after auto-transition. Returns list of message_ids to escalate.
    """
    if new_state not in ESCALATION_STATES:
        return []

    escalations = _pending_escalations.get(agent_id, [])
    return [e["message_id"] for e in escalations]


def escalate_to_blocking(agent_id: str, message_id: str) -> None:
    """Promote a non-blocking message to blocking.

    Sets inbox_blocked and writes a critical annotation.
    """
    # Find the escalation entry
    escalations = _pending_escalations.get(agent_id, [])
    entry = next((e for e in escalations if e["message_id"] == message_id), None)
    if not entry:
        return

    from_agent = entry.get("from_agent", "unknown")
    message_type = entry.get("message_type", "unknown")

    # Set inbox_blocked
    agent_state_path = AGENTS_DIR / f"{agent_id}.json"
    if agent_state_path.exists():

        def _set_blocked(data: dict) -> dict:
            data["inbox_blocked"] = {
                "blocked_by_message_id": message_id,
                "from_agent": from_agent,
            }
            return data

        locked_read_modify_write(agent_state_path, _set_blocked)
        _invalidate_agent_cache(agent_id)

    # Write critical annotation
    annotation = {
        "priority": "critical",
        "message": (
            f"ESCALATED: {message_type} from {from_agent} now blocking. "
            f"Read inbox via PTC, then ack: send-msg ack --message-id {message_id}"
        ),
        "from_agent": from_agent,
        "annotation_id": f"esc-{message_id[:8]}",
        "acknowledged": False,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    ann_file = ANNOTATIONS_DIR / f"{agent_id}.jsonl"
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with ann_file.open("a") as f:
            f.write(json.dumps(annotation, default=str) + "\n")
    except Exception:
        pass

    # Remove from pending escalations
    _pending_escalations[agent_id] = [
        e for e in escalations if e["message_id"] != message_id
    ]

    # Emit event
    emit(
        "message_escalated",
        agent=agent_id,
        message_id=message_id,
        trigger_state="escalated",
    )
