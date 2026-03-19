"""Tests for scripts/send_msg.py — file-based agent messaging CLI."""

from __future__ import annotations

import json
import threading
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.send_msg import (
    _read_inbox,
    _resolve_inbox_path,
    cmd_ack,
    cmd_count,
    cmd_list,
    cmd_send,
    cmd_team,
)


@pytest.fixture()
def msg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up env vars and redirect inbox paths to tmp_path."""
    monkeypatch.setenv("AGENT_NAME", "orchestrator-01")
    monkeypatch.setenv("TEAM_ID", "team-abc")

    # Redirect _MESSAGES_ROOT to tmp_path
    import scripts.send_msg as mod

    monkeypatch.setattr(mod, "_MESSAGES_ROOT", tmp_path)

    # Create inbox directory
    inbox_dir = tmp_path / "team-abc"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    return tmp_path


def _make_send_args(
    to: str = "coder-01",
    msg_type: str = "task_assign",
    priority: str = "blocking",
    body: str | None = None,
    payload_file: str | None = None,
    reply_to: str | None = None,
) -> Namespace:
    if body is None and payload_file is None:
        body = json.dumps({"task_type": "implement", "instructions": "do X"})
    return Namespace(
        to=to,
        type=msg_type,
        priority=priority,
        body=body,
        payload_file=payload_file,
        reply_to=reply_to,
    )


@patch("scripts.send_msg._notify_daemon")
def test_send_and_count_round_trip(mock_daemon, msg_env, capsys):
    """Send a message, then count — verify total=1, unread=1, blocking_unread=1."""
    cmd_send(_make_send_args(to="orchestrator-01"))
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True
    assert "message_id" in out
    assert out["sequence"] == 1

    # Count
    cmd_count(Namespace())
    count_out = json.loads(capsys.readouterr().out.strip())
    assert count_out["total"] == 1
    assert count_out["unread"] == 1
    assert count_out["blocking_unread"] == 1

    mock_daemon.assert_called_once()


@patch("scripts.send_msg._notify_daemon")
def test_send_validates_body(mock_daemon, msg_env):
    """Sending with invalid body for task_assign should error."""
    # task_assign requires task_type and instructions
    bad_body = json.dumps({"wrong_field": "value"})
    args = _make_send_args(body=bad_body)

    with pytest.raises(SystemExit):
        cmd_send(args)


@patch("scripts.send_msg._notify_daemon")
def test_ack_marks_read(mock_daemon, msg_env, capsys):
    """Send a message, ack it, verify count shows unread=0."""
    # Send to our own inbox so we can ack it
    cmd_send(_make_send_args(to="orchestrator-01"))
    send_out = json.loads(capsys.readouterr().out.strip())
    msg_id = send_out["message_id"]

    # Ack
    cmd_ack(Namespace(message_id=msg_id))
    ack_out = json.loads(capsys.readouterr().out.strip())
    assert ack_out["ok"] is True

    # Count should show 0 unread
    cmd_count(Namespace())
    count_out = json.loads(capsys.readouterr().out.strip())
    assert count_out["total"] == 1
    assert count_out["unread"] == 0
    assert count_out["blocking_unread"] == 0


@patch("scripts.send_msg._notify_daemon")
def test_list_unread_only(mock_daemon, msg_env, capsys):
    """Send 2 messages, ack 1, list --unread-only should return 1."""
    # Send two messages to own inbox
    cmd_send(_make_send_args(to="orchestrator-01"))
    out1 = json.loads(capsys.readouterr().out.strip())

    cmd_send(_make_send_args(to="orchestrator-01"))
    capsys.readouterr()  # discard

    # Ack first message
    cmd_ack(Namespace(message_id=out1["message_id"]))
    capsys.readouterr()  # discard

    # List unread only
    cmd_list(Namespace(unread_only=True, type=None))
    list_out = json.loads(capsys.readouterr().out.strip())
    assert len(list_out) == 1
    assert list_out[0]["read"] is False


@patch("scripts.send_msg._notify_daemon")
def test_list_filter_by_type(mock_daemon, msg_env, capsys):
    """Send messages of different types, filter by one type."""
    # Send task_assign
    cmd_send(_make_send_args(to="orchestrator-01", msg_type="task_assign"))
    capsys.readouterr()

    # Send shutdown
    shutdown_body = json.dumps({"reason": "done"})
    cmd_send(
        _make_send_args(
            to="orchestrator-01",
            msg_type="shutdown",
            priority="non-blocking",
            body=shutdown_body,
        )
    )
    capsys.readouterr()

    # Filter by shutdown
    cmd_list(Namespace(unread_only=False, type="shutdown"))
    list_out = json.loads(capsys.readouterr().out.strip())
    assert len(list_out) == 1
    assert list_out[0]["message_type"] == "shutdown"


@patch("scripts.send_msg._notify_daemon")
def test_concurrent_sends(mock_daemon, msg_env):
    """Send 10 messages concurrently to same inbox, verify all present with unique sequences."""
    errors: list[Exception] = []
    lock = threading.Lock()

    def send_one(i: int) -> None:
        try:
            import io
            import sys

            body = json.dumps({"task_type": "implement", "instructions": f"task {i}"})
            args = _make_send_args(to="orchestrator-01", body=body)

            # Redirect stdout per thread to avoid capsys conflicts
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            try:
                cmd_send(args)
            finally:
                sys.stdout = old_stdout
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=send_one, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Errors during concurrent sends: {errors}"

    # Verify inbox has exactly 10 messages with unique sequences
    inbox_path = _resolve_inbox_path("team-abc", "orchestrator-01")
    messages = _read_inbox(inbox_path)
    assert len(messages) == 10

    sequences = [m["sequence"] for m in messages]
    assert len(set(sequences)) == 10


def test_missing_env_vars(tmp_path, monkeypatch, capsys):
    """Verify error when AGENT_NAME or TEAM_ID not set."""
    # Clear any existing env vars
    monkeypatch.delenv("AGENT_NAME", raising=False)
    monkeypatch.delenv("TEAM_ID", raising=False)

    args = _make_send_args()
    with pytest.raises(SystemExit):
        cmd_send(args)

    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
    assert "AGENT_NAME" in out["error"]
    assert "TEAM_ID" in out["error"]


@patch("scripts.send_msg._notify_daemon")
def test_send_with_payload_file(mock_daemon, msg_env, tmp_path, capsys):
    """Send a message using --payload-file instead of --body."""
    payload = {"task_type": "implement", "instructions": "from file"}
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))

    args = _make_send_args(
        to="orchestrator-01", body=None, payload_file=str(payload_path)
    )
    cmd_send(args)

    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True

    # Verify envelope has lightweight body + content_path
    inbox_path = _resolve_inbox_path("team-abc", "orchestrator-01")
    messages = _read_inbox(inbox_path)
    assert len(messages) == 1
    assert messages[0]["body"]["task_type"] == "implement"
    # Full payload is at content_path, not inlined in body
    assert messages[0]["content_path"] is not None
    content = json.loads(Path(messages[0]["content_path"]).read_text())
    assert content["instructions"] == "from file"


@patch("scripts.send_msg._notify_daemon")
def test_resolve_role_from_manifest(mock_daemon, msg_env, monkeypatch, capsys):
    """--to-role resolves via manifest when daemon unavailable."""
    # Create a manifest with a coder
    import scripts.send_msg as mod

    manifest_dir = mod._MESSAGES_ROOT / "team-abc"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "team_id": "team-abc",
        "workflow_id": "wf-1",
        "created_at": "2024-01-01T00:00:00Z",
        "created_by": "orch-01",
        "agents": {
            "coder-x1y2": {
                "role": "coder",
                "status": "active",
                "joined_at": "2024-01-01T00:00:00Z",
                "inbox_path": str(manifest_dir / "coder-x1y2.inbox.json"),
            }
        },
    }
    (manifest_dir / "_manifest.json").write_text(json.dumps(manifest))

    # Send by role
    args = _make_send_args(to=None)
    args.to = None
    args.to_role = "coder"
    cmd_send(args)

    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True

    # Verify message went to coder-x1y2's inbox
    inbox = _read_inbox(_resolve_inbox_path("team-abc", "coder-x1y2"))
    assert len(inbox) == 1


@patch("scripts.send_msg._notify_daemon")
def test_team_list_from_manifest(mock_daemon, msg_env, capsys):
    """team subcommand reads manifest when daemon unavailable."""
    import scripts.send_msg as mod

    manifest_dir = mod._MESSAGES_ROOT / "team-abc"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "team_id": "team-abc",
        "workflow_id": "wf-1",
        "created_at": "2024-01-01T00:00:00Z",
        "created_by": "orch-01",
        "agents": {
            "orchestrator-01": {
                "role": "orchestrator",
                "status": "active",
                "joined_at": "2024-01-01T00:00:00Z",
                "inbox_path": "/tmp/x",
            },
            "coder-a7f2": {
                "role": "coder",
                "status": "active",
                "joined_at": "2024-01-01T00:00:00Z",
                "inbox_path": "/tmp/y",
            },
        },
    }
    (manifest_dir / "_manifest.json").write_text(json.dumps(manifest))

    cmd_team(Namespace())
    out = json.loads(capsys.readouterr().out.strip())
    assert out["team_id"] == "team-abc"
    assert len(out["members"]) == 2
    roles = {m["role"] for m in out["members"]}
    assert "orchestrator" in roles
    assert "coder" in roles
