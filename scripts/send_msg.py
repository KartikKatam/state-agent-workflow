#!/usr/bin/env python3
"""CLI script for file-based inter-agent messaging.

Agents call this via Bash tool to send, acknowledge, list, and count messages.
Messages are stored as JSON arrays in inbox files:
    ~/.claude/messages/{team_id}/{agent}.inbox.json

Agent identity comes from env vars: AGENT_NAME, TEAM_ID
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, ValidationError

from schemas.message_protocol import (
    BugReportPayload,
    ContextQueryPayload,
    ContextResponsePayload,
    HandoffPayload,
    InfoReadyPayload,
    InfoRequestPayload,
    PeerNotifyPayload,
    ShutdownPayload,
    StatusUpdatePayload,
    TaskAssignPayload,
    TaskCompletePayload,
)
from schemas.messaging import MessageEnvelope

# ---------------------------------------------------------------------------
# Payload model registry
# ---------------------------------------------------------------------------

_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "task_assign": TaskAssignPayload,
    "task_complete": TaskCompletePayload,
    "info_request": InfoRequestPayload,
    "info_ready": InfoReadyPayload,
    "status_update": StatusUpdatePayload,
    "context_query": ContextQueryPayload,
    "context_response": ContextResponsePayload,
    "handoff": HandoffPayload,
    "shutdown": ShutdownPayload,
    "peer_notify": PeerNotifyPayload,
    "bug_report": BugReportPayload,
}

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_MESSAGES_ROOT = Path(os.path.expanduser("~/.claude/messages"))


def _resolve_inbox_path(team_id: str, agent_name: str) -> Path:
    """Return inbox path: ~/.claude/messages/{team_id}/{agent}.inbox.json"""
    return _MESSAGES_ROOT / team_id / f"{agent_name}.inbox.json"


def _resolve_draft_path(team_id: str, agent_name: str) -> Path:
    """Return draft file path: ~/.claude/messages/{team_id}/{agent}.draft.json

    Agents write their message payload here before calling send.
    The file is overwritten each time — one draft per agent, zero temp proliferation.
    """
    return _MESSAGES_ROOT / team_id / f"{agent_name}.draft.json"


def _resolve_payloads_dir(team_id: str) -> Path:
    """Return payloads dir: ~/.claude/messages/{team_id}/payloads/"""
    return _MESSAGES_ROOT / team_id / "payloads"


def _store_payload(team_id: str, message_id: str, content: dict) -> str:
    """Copy validated payload to permanent storage. Returns the stored path."""
    payloads_dir = _resolve_payloads_dir(team_id)
    payloads_dir.mkdir(parents=True, exist_ok=True)
    payload_path = payloads_dir / f"{message_id}.json"
    fd, tmp = tempfile.mkstemp(dir=payloads_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(content, f, indent=2, default=str)
        os.replace(tmp, payload_path)
    except BaseException:
        os.unlink(tmp)
        raise
    return str(payload_path)


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _require_env() -> tuple[str, str]:
    """Read AGENT_NAME and TEAM_ID from env. Exits on missing."""
    agent_name = os.environ.get("AGENT_NAME")
    team_id = os.environ.get("TEAM_ID")
    missing = []
    if not agent_name:
        missing.append("AGENT_NAME")
    if not team_id:
        missing.append("TEAM_ID")
    if missing:
        _error(f"Missing required env vars: {', '.join(missing)}")
    return agent_name, team_id  # type: ignore[return-value]


def _error(msg: str) -> None:
    """Print error JSON and exit."""
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_body(message_type: str, body: dict) -> dict:
    """Validate body against the Pydantic payload model for message_type.

    Returns the validated (and normalized) body dict.
    Raises SystemExit on validation failure.
    """
    model_cls = _PAYLOAD_MODELS.get(message_type)
    if model_cls is None:
        _error(f"Unknown message_type: {message_type}")
    try:
        validated = model_cls.model_validate(body)  # type: ignore[union-attr]
        return validated.model_dump(exclude_defaults=True, exclude_none=True)
    except ValidationError as e:
        _error(f"Body validation failed for {message_type}: {e}")
    return {}  # unreachable, satisfies type checker


# ---------------------------------------------------------------------------
# Sequence tracking
# ---------------------------------------------------------------------------


def _extract_envelope_body(message_type: str, validated_body: dict) -> dict:
    """Extract lightweight metadata for the envelope body.

    The full payload lives at content_path. The envelope body contains only
    fields useful for routing and quick inspection without reading the file.
    """
    # Per-type metadata extraction — keep envelope small
    meta: dict = {}
    if message_type == "task_assign":
        if "task_type" in validated_body:
            meta["task_type"] = validated_body["task_type"]
    elif message_type == "task_complete":
        if "status" in validated_body:
            meta["status"] = validated_body["status"]
    elif message_type == "info_request":
        if "request_type" in validated_body:
            meta["request_type"] = validated_body["request_type"]
        if "priority" in validated_body:
            meta["priority"] = validated_body["priority"]
    elif message_type == "info_ready":
        if "status" in validated_body:
            meta["status"] = validated_body["status"]
    elif message_type == "status_update":
        if "phase" in validated_body:
            meta["phase"] = validated_body["phase"]
    elif message_type == "handoff":
        if "reason" in validated_body:
            meta["reason"] = validated_body["reason"]
    elif message_type == "shutdown":
        if "reason" in validated_body:
            meta["reason"] = validated_body["reason"]
    elif message_type == "peer_notify":
        if "urgency" in validated_body:
            meta["urgency"] = validated_body["urgency"]
    elif message_type == "bug_report":
        if "scenario_label" in validated_body:
            meta["scenario_label"] = validated_body["scenario_label"]
    # context_query, context_response: no special metadata needed
    return meta


def _get_next_sequence(messages: list[dict]) -> int:
    """Return max(existing sequences, default 0) + 1."""
    if not messages:
        return 1
    return max(m.get("sequence", 0) for m in messages) + 1


# ---------------------------------------------------------------------------
# Locked inbox operations
# ---------------------------------------------------------------------------

# flock is per-process, not per-thread. Use a threading lock for in-process concurrency.
import threading

_inbox_thread_lock = threading.Lock()


def _locked_append_message(inbox_path: Path, envelope_dict: dict) -> int:
    """Append a message to an inbox file with exclusive file locking.

    Creates the inbox file if it doesn't exist.
    Returns the assigned sequence number.

    Uses a dedicated lock file to avoid TOCTOU races when multiple
    threads/processes create the inbox simultaneously.
    """
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = inbox_path.with_suffix(".lock")

    # Thread lock (flock is per-process, doesn't serialize threads)
    with _inbox_thread_lock:
        with open(lock_path, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                # Read existing messages (or start fresh)
                messages: list[dict] = []
                if inbox_path.exists():
                    content = inbox_path.read_text().strip()
                    if content:
                        messages = json.loads(content)

                seq = _get_next_sequence(messages)
                envelope_dict["sequence"] = seq
                messages.append(envelope_dict)

                # Write back atomically via temp file + rename
                fd, tmp = tempfile.mkstemp(dir=inbox_path.parent, suffix=".tmp")
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(messages, f, indent=2, default=str)
                    os.replace(tmp, inbox_path)
                except BaseException:
                    os.unlink(tmp)
                    raise

                return seq
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)


def _locked_mark_read(inbox_path: Path, message_id: str) -> bool:
    """Set read=True for a message by message_id. Returns True if found."""
    if not inbox_path.exists():
        return False

    found = False
    with open(inbox_path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            messages: list[dict] = json.load(f)
            for msg in messages:
                if msg.get("message_id") == message_id:
                    msg["read"] = True
                    found = True
                    break
            if found:
                f.seek(0)
                f.truncate()
                json.dump(messages, f, indent=2, default=str)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return found


def _read_inbox(inbox_path: Path) -> list[dict]:
    """Read all messages from an inbox file. Returns empty list if missing."""
    if not inbox_path.exists():
        return []
    try:
        with open(inbox_path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Daemon notification (fire and forget)
# ---------------------------------------------------------------------------


def _notify_daemon(
    from_agent: str,
    to_agent: str,
    team_id: str,
    message_id: str,
    priority: str,
    message_type: str,
    command: str = "message_notification",
) -> None:
    """Notify daemon about a message event. Silently fails if daemon unavailable."""
    workflow_id = os.environ.get("WORKFLOW_ID", "default")
    try:
        from scripts.daemon.server import send_to_daemon

        send_to_daemon(
            workflow_id,
            {
                "command": command,
                "from_agent": from_agent,
                "to_agent": to_agent,
                "team_id": team_id,
                "message_id": message_id,
                "priority": priority,
                "message_type": message_type,
            },
        )
    except Exception:
        pass  # fire and forget


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _resolve_role(team_id: str, role: str) -> str:
    """Resolve a role to an agent_id via the daemon session registry.

    If multiple agents have the same role, returns the first one.
    Falls back to reading the team manifest if daemon is unavailable.
    """
    workflow_id = os.environ.get("WORKFLOW_ID", "default")

    # Try daemon first
    try:
        from scripts.daemon.server import send_to_daemon

        result = send_to_daemon(
            workflow_id,
            {
                "command": "resolve_role",
                "team_id": team_id,
                "role": role,
            },
        )
        if result and result.get("ok") and result.get("agent_ids"):
            return result["agent_ids"][0]
    except Exception:
        pass

    # Fallback: read manifest directly
    manifest_path = _MESSAGES_ROOT / team_id / "_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            agents = manifest.get("agents", {})
            for agent_id, info in agents.items():
                if (
                    isinstance(info, dict)
                    and info.get("role") == role
                    and info.get("status") == "active"
                ):
                    return agent_id
        except (json.JSONDecodeError, OSError):
            pass

    _error(f"No active {role} found in team {team_id}")
    return ""  # unreachable


def cmd_send(args: argparse.Namespace) -> None:
    """Send a message to another agent's inbox.

    Payload source priority:
    1. --body '{json}'           (inline, for simple messages)
    2. --payload-file /path      (explicit file)
    3. draft file                (default — agent's reusable scratchpad)
    """
    agent_name, team_id = _require_env()

    # Resolve recipient
    if args.to:
        recipient = args.to
    elif getattr(args, "to_role", None):
        recipient = _resolve_role(team_id, args.to_role)
    else:
        _error("Must provide --to or --to-role")
        return

    # Resolve payload from one of three sources
    body: dict
    if args.payload_file:
        try:
            body = json.loads(Path(args.payload_file).read_text())
        except (json.JSONDecodeError, OSError) as e:
            _error(f"Failed to read payload file: {e}")
            return  # unreachable
    elif args.body:
        try:
            body = json.loads(args.body)
        except json.JSONDecodeError as e:
            _error(f"Invalid JSON in --body: {e}")
            return  # unreachable
    else:
        # Default: read from agent's draft file
        draft_path = _resolve_draft_path(team_id, agent_name)
        if not draft_path.exists():
            _error(
                f"No --body, --payload-file, or draft file. "
                f"Write payload to {draft_path} first, or use --body."
            )
            return  # unreachable
        try:
            body = json.loads(draft_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            _error(f"Failed to read draft file {draft_path}: {e}")
            return  # unreachable

    # Validate body against payload model
    validated_body = _validate_body(args.type, body)

    # Build envelope
    message_id = str(uuid4())
    now = datetime.now(timezone.utc)

    # Store full payload to permanent file, keep envelope lightweight
    content_path = _store_payload(team_id, message_id, validated_body)

    # Envelope body contains only routing-useful metadata (not the full payload)
    envelope_body = _extract_envelope_body(args.type, validated_body)

    envelope = MessageEnvelope(
        message_id=message_id,
        sequence=1,  # placeholder, overwritten by _locked_append_message
        from_agent=agent_name,
        to_agent=recipient,
        team_id=team_id,
        message_type=args.type,
        priority=args.priority,
        reply_to=args.reply_to,
        timestamp=now,
        read=False,
        body=envelope_body,
        content_path=content_path,
    )
    envelope_dict = json.loads(envelope.model_dump_json())

    # Append to recipient inbox
    inbox_path = _resolve_inbox_path(team_id, recipient)
    seq = _locked_append_message(inbox_path, envelope_dict)

    # Notify daemon
    _notify_daemon(
        from_agent=agent_name,
        to_agent=recipient,
        team_id=team_id,
        message_id=message_id,
        priority=args.priority,
        message_type=args.type,
    )

    print(json.dumps({"ok": True, "message_id": message_id, "sequence": seq, "content_path": content_path}))


def cmd_ack(args: argparse.Namespace) -> None:
    """Acknowledge a message (set read=True)."""
    agent_name, team_id = _require_env()
    inbox_path = _resolve_inbox_path(team_id, agent_name)

    found = _locked_mark_read(inbox_path, args.message_id)
    if not found:
        _error(f"Message not found: {args.message_id}")
        return

    # Notify daemon
    _notify_daemon(
        from_agent=agent_name,
        to_agent=agent_name,
        team_id=team_id,
        message_id=args.message_id,
        priority="non-blocking",
        message_type="",
        command="message_ack",
    )

    print(json.dumps({"ok": True, "message_id": args.message_id}))


def cmd_list(args: argparse.Namespace) -> None:
    """List messages in own inbox."""
    agent_name, team_id = _require_env()
    inbox_path = _resolve_inbox_path(team_id, agent_name)
    messages = _read_inbox(inbox_path)

    # Apply filters
    if args.unread_only:
        messages = [m for m in messages if not m.get("read", False)]
    if args.type:
        messages = [m for m in messages if m.get("message_type") == args.type]

    print(json.dumps(messages, indent=2, default=str))


def cmd_count(_args: argparse.Namespace) -> None:
    """Count messages in own inbox."""
    agent_name, team_id = _require_env()
    inbox_path = _resolve_inbox_path(team_id, agent_name)
    messages = _read_inbox(inbox_path)

    total = len(messages)
    unread = sum(1 for m in messages if not m.get("read", False))
    blocking_unread = sum(
        1
        for m in messages
        if not m.get("read", False) and m.get("priority") == "blocking"
    )

    print(
        json.dumps(
            {"total": total, "unread": unread, "blocking_unread": blocking_unread}
        )
    )


def cmd_team(_args: argparse.Namespace) -> None:
    """List active teammates in the current team."""
    agent_name, team_id = _require_env()
    workflow_id = os.environ.get("WORKFLOW_ID", "default")

    # Try daemon registry first (most accurate — has liveness data)
    try:
        from scripts.daemon.server import send_to_daemon

        result = send_to_daemon(
            workflow_id,
            {
                "command": "team_members",
                "team_id": team_id,
            },
        )
        if result and result.get("ok"):
            print(
                json.dumps(
                    {
                        "team_id": team_id,
                        "source": "daemon_registry",
                        "members": result["members"],
                    },
                    indent=2,
                    default=str,
                )
            )
            return
    except Exception:
        pass

    # Fallback: read manifest
    manifest_path = _MESSAGES_ROOT / team_id / "_manifest.json"
    if not manifest_path.exists():
        _error(f"Team {team_id} not found")
        return

    try:
        manifest = json.loads(manifest_path.read_text())
        members = []
        for agent_id, info in manifest.get("agents", {}).items():
            if isinstance(info, dict):
                members.append(
                    {
                        "agent_id": agent_id,
                        "role": info.get("role", "unknown"),
                        "status": info.get("status", "unknown"),
                    }
                )
        print(
            json.dumps(
                {
                    "team_id": team_id,
                    "source": "manifest",
                    "members": members,
                },
                indent=2,
                default=str,
            )
        )
    except (json.JSONDecodeError, OSError) as e:
        _error(f"Failed to read manifest: {e}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="send-msg",
        description="File-based inter-agent messaging CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- send ---
    p_send = sub.add_parser("send", help="Send a message to another agent")
    p_send.add_argument("--to", default=None, help="Recipient agent name (direct)")
    p_send.add_argument("--to-role", default=None, help="Recipient role (resolved via daemon)")
    p_send.add_argument("--type", required=True, help="Message type")
    p_send.add_argument(
        "--priority",
        required=True,
        choices=["blocking", "non-blocking"],
        help="Message priority",
    )
    p_send.add_argument("--body", help="JSON body string")
    p_send.add_argument("--payload-file", help="Path to JSON file with body")
    p_send.add_argument("--reply-to", default=None, help="Message ID being replied to")

    # --- ack ---
    p_ack = sub.add_parser("ack", help="Acknowledge a message")
    p_ack.add_argument("--message-id", required=True, help="Message ID to acknowledge")

    # --- list ---
    p_list = sub.add_parser("list", help="List messages in inbox")
    p_list.add_argument("--unread-only", action="store_true", help="Only show unread")
    p_list.add_argument("--type", default=None, help="Filter by message type")

    # --- count ---
    sub.add_parser("count", help="Count messages in inbox")

    # --- team ---
    sub.add_parser("team", help="List teammates in current team")

    args = parser.parse_args()

    if args.command == "send":
        cmd_send(args)
    elif args.command == "ack":
        cmd_ack(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "count":
        cmd_count(args)
    elif args.command == "team":
        cmd_team(args)


if __name__ == "__main__":
    main()
