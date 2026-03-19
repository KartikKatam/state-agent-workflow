"""Socket server, request handler, and CLI main().

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import socketserver
import threading
import time
from pathlib import Path

from scripts.daemon.permissions import check_tool_allowed, handle_pre_tool
from scripts.daemon import state_manager as _sm
from scripts.daemon.state_manager import (
    _socket_path,
    _pid_path,
    get_agent_state,
    get_system_state,
    load_machine,
    register_agent,
    update_agent_context,
)
from scripts.daemon.messaging import handle_message_ack, handle_message_notification
from scripts.daemon.transitions import do_transition, do_system_transition, handle_post_tool
from scripts.daemon.validators import _validate_post_actions


# ---------------------------------------------------------------------------
# Request dispatcher (shared by daemon and direct mode)
# ---------------------------------------------------------------------------
def process_request(request: dict) -> dict:
    """Route a request to the appropriate handler."""
    cmd = request.get("command", "")

    if cmd == "check_tool":
        return check_tool_allowed(
            request["agent_id"],
            request["tool"],
            request.get("tool_input", {}),
        )
    elif cmd == "pre_tool":
        return handle_pre_tool(
            request["agent_id"],
            request["tool"],
            request.get("tool_input", {}),
        )
    elif cmd == "post_tool":
        return handle_post_tool(
            request["agent_id"],
            request["tool"],
            request.get("tool_input", {}),
            request.get("tool_output_summary", ""),
        )
    elif cmd == "transition":
        return do_transition(
            request["agent_id"],
            request["to_state"],
            request["trigger"],
        )
    elif cmd == "system_transition":
        return do_system_transition(
            request["to_state"],
            request["trigger"],
        )
    elif cmd == "register":
        return register_agent(
            agent_id=request["agent_id"],
            role=request["role"],
            model=request["model"],
            phase=request.get("phase"),
            task=request.get("task"),
            parent_agent_id=request.get("parent_agent_id"),
            worktree=request.get("worktree"),
            base_branch=request.get("base_branch"),
            team_id=request.get("team_id"),
            workflow_id=request.get("workflow_id"),
        )
    elif cmd == "update_context":
        return update_agent_context(
            request["agent_id"],
            request["input_tokens"],
            request["output_tokens"],
        )
    elif cmd == "get_state":
        agent = get_agent_state(request["agent_id"])
        if agent:
            return {"ok": True, "state": json.loads(agent.model_dump_json())}
        return {"ok": False, "reason": "Agent state not found"}
    elif cmd == "system_state":
        state = get_system_state()
        if state:
            return {"ok": True, "state": json.loads(state.model_dump_json())}
        return {"ok": False, "reason": "System state not found"}
    elif cmd == "message_notification":
        return handle_message_notification(
            request["from_agent"],
            request["to_agent"],
            request["team_id"],
            request["message_id"],
            request["priority"],
            request["message_type"],
        )
    elif cmd == "message_ack":
        return handle_message_ack(
            request.get("agent_id") or request.get("from_agent", ""),
            request["message_id"],
            request.get("team_id", ""),
        )
    elif cmd == "team_members":
        from scripts.daemon.state_manager import get_team_sessions
        return {
            "ok": True,
            "members": get_team_sessions(request["team_id"]),
        }
    elif cmd == "resolve_role":
        from scripts.daemon.state_manager import resolve_role_in_team
        agents = resolve_role_in_team(request["team_id"], request["role"])
        if not agents:
            return {"ok": False, "reason": f"No active {request['role']} in team {request['team_id']}"}
        return {"ok": True, "agent_ids": agents}
    elif cmd == "depart_session":
        from scripts.daemon.state_manager import depart_session
        depart_session(request["agent_id"])
        return {"ok": True}
    else:
        return {"ok": False, "reason": f"Unknown command: {cmd}"}


# ---------------------------------------------------------------------------
# Daemon server
# ---------------------------------------------------------------------------
class RequestHandler(socketserver.StreamRequestHandler):
    """Handle one JSON request per connection."""

    def handle(self) -> None:
        try:
            raw = self.rfile.readline().decode().strip()
            if not raw:
                return
            request = json.loads(raw)
            response = process_request(request)
            self.wfile.write((json.dumps(response) + "\n").encode())
        except Exception as e:
            error = {"ok": False, "allowed": True, "reason": f"daemon error: {e}"}
            self.wfile.write((json.dumps(error) + "\n").encode())


class WorkflowDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Threaded Unix socket server for state machine queries."""

    daemon_threads = True
    allow_reuse_address = True


_shutdown_event = threading.Event()


def serve(workflow_id: str, timeout: int = 3600) -> None:
    """Start the daemon. Blocks until stopped or timeout."""
    sock_path = _socket_path(workflow_id)
    pid_path = _pid_path(workflow_id)

    # Clean up stale socket
    if os.path.exists(sock_path):
        os.unlink(sock_path)

    # Write PID file
    Path(pid_path).write_text(str(os.getpid()))

    # Pre-load all machine definitions
    for f in _sm.MACHINES_DIR.glob("*.json"):
        load_machine(f.stem)

    # Validate post_actions reference valid validator names
    _validate_post_actions()

    server = WorkflowDaemon(sock_path, RequestHandler)

    # Handle SIGTERM for clean shutdown
    def handle_signal(_signum: int, _frame: object) -> None:
        _shutdown_event.set()
        server.shutdown()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Auto-shutdown timer
    def auto_shutdown() -> None:
        if not _shutdown_event.wait(timeout):
            server.shutdown()

    timer = threading.Thread(target=auto_shutdown, daemon=True)
    timer.start()

    try:
        server.serve_forever()
    finally:
        # Cleanup
        server.server_close()
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        if os.path.exists(pid_path):
            os.unlink(pid_path)


def stop_daemon(workflow_id: str) -> dict:
    """Stop a running daemon by sending SIGTERM."""
    pid_path = _pid_path(workflow_id)
    if not os.path.exists(pid_path):
        return {"ok": False, "reason": "No PID file found — daemon not running?"}

    pid = int(Path(pid_path).read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for cleanup
        for _ in range(20):
            time.sleep(0.1)
            if not os.path.exists(pid_path):
                return {"ok": True, "reason": f"Daemon (PID {pid}) stopped"}
        return {"ok": True, "reason": f"SIGTERM sent to PID {pid}"}
    except ProcessLookupError:
        # Process already gone, clean up files
        if os.path.exists(pid_path):
            os.unlink(pid_path)
        sock = _socket_path(workflow_id)
        if os.path.exists(sock):
            os.unlink(sock)
        return {"ok": True, "reason": "Daemon already stopped, cleaned up files"}


def daemon_status(workflow_id: str) -> dict:
    """Check if daemon is running."""
    pid_path = _pid_path(workflow_id)
    if not os.path.exists(pid_path):
        return {"running": False}

    pid = int(Path(pid_path).read_text().strip())
    try:
        os.kill(pid, 0)  # Check if process exists
        return {"running": True, "pid": pid, "socket": _socket_path(workflow_id)}
    except ProcessLookupError:
        return {"running": False, "stale_pid": pid}


# ---------------------------------------------------------------------------
# Client: route through daemon or execute directly
# ---------------------------------------------------------------------------
def send_to_daemon(workflow_id: str, request: dict) -> dict | None:
    """Send request to daemon via Unix socket. Returns None if daemon unavailable."""
    sock_path = _socket_path(workflow_id)
    if not os.path.exists(sock_path):
        return None

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(sock_path)
        s.sendall((json.dumps(request) + "\n").encode())
        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\n" in response:
                break
        s.close()
        return json.loads(response.decode().strip())
    except Exception:
        return None


def execute(request: dict, workflow_id: str = "default") -> dict:
    """Execute a request: try daemon first, fall back to direct."""
    result = send_to_daemon(workflow_id, request)
    if result is not None:
        return result
    return process_request(request)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _detect_workflow_id() -> str:
    """Try to detect workflow ID from system state file."""
    state = get_system_state()
    if state:
        return state.workflow_id
    wf_id = os.environ.get("WORKFLOW_ID", "default")
    if not re.match(r"^[a-zA-Z0-9_-]+$", wf_id):
        wf_id = "default"
    return wf_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Workflow state machine enforcement")
    sub = parser.add_subparsers(dest="command", required=True)

    # --- serve ---
    p_serve = sub.add_parser("serve", help="Start the daemon")
    p_serve.add_argument("--workflow-id", default=None)
    p_serve.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Auto-shutdown after N seconds of inactivity (default: 3600)",
    )

    # --- stop ---
    p_stop = sub.add_parser("stop", help="Stop the daemon")
    p_stop.add_argument("--workflow-id", default=None)

    # --- status ---
    p_status = sub.add_parser("status", help="Check daemon status")
    p_status.add_argument("--workflow-id", default=None)

    # --- check-tool ---
    p_check = sub.add_parser("check-tool", help="Check if a tool call is allowed")
    p_check.add_argument("--agent-id", required=True)
    p_check.add_argument("--tool", required=True)
    p_check.add_argument("--file-path", default="")
    p_check.add_argument("--workflow-id", default=None)

    # --- register ---
    p_reg = sub.add_parser("register", help="Register a new agent")
    p_reg.add_argument("--agent-id", required=True)
    p_reg.add_argument("--role", required=True)
    p_reg.add_argument("--model", required=True)
    p_reg.add_argument("--phase", default=None)
    p_reg.add_argument("--task", default=None)
    p_reg.add_argument("--parent-agent-id", default=None)
    p_reg.add_argument("--worktree", default=None)
    p_reg.add_argument("--base-branch", default=None)
    p_reg.add_argument("--workflow-id", default=None)

    # --- update-context ---
    p_ctx = sub.add_parser("update-context", help="Update agent token counts")
    p_ctx.add_argument("--agent-id", required=True)
    p_ctx.add_argument("--input-tokens", type=int, required=True)
    p_ctx.add_argument("--output-tokens", type=int, required=True)
    p_ctx.add_argument("--workflow-id", default=None)

    # --- get-state ---
    p_get = sub.add_parser("get-state", help="Get agent state")
    p_get.add_argument("--agent-id", required=True)
    p_get.add_argument("--workflow-id", default=None)

    # --- system-transition ---
    p_sys = sub.add_parser("system-transition", help="System state transition")
    p_sys.add_argument("--to-state", required=True)
    p_sys.add_argument("--trigger", required=True)
    p_sys.add_argument("--workflow-id", default=None)

    # --- system-state ---
    p_sstate = sub.add_parser("system-state", help="Get system state")
    p_sstate.add_argument("--workflow-id", default=None)

    args = parser.parse_args()
    wf_id = getattr(args, "workflow_id", None) or _detect_workflow_id()

    # Daemon lifecycle commands
    if args.command == "serve":
        serve(wf_id, args.timeout)
        return
    if args.command == "stop":
        print(json.dumps(stop_daemon(wf_id)))
        return
    if args.command == "status":
        print(json.dumps(daemon_status(wf_id)))
        return

    # Build request from CLI args
    request: dict = {}
    if args.command == "check-tool":
        request = {
            "command": "check_tool",
            "agent_id": args.agent_id,
            "tool": args.tool,
            "tool_input": {"file_path": args.file_path} if args.file_path else {},
        }
    elif args.command == "register":
        request = {
            "command": "register",
            "agent_id": args.agent_id,
            "role": args.role,
            "model": args.model,
            "phase": args.phase,
            "task": args.task,
            "parent_agent_id": args.parent_agent_id,
            "worktree": args.worktree,
            "base_branch": args.base_branch,
        }
    elif args.command == "update-context":
        request = {
            "command": "update_context",
            "agent_id": args.agent_id,
            "input_tokens": args.input_tokens,
            "output_tokens": args.output_tokens,
        }
    elif args.command == "get-state":
        request = {"command": "get_state", "agent_id": args.agent_id}
    elif args.command == "system-transition":
        request = {
            "command": "system_transition",
            "to_state": args.to_state,
            "trigger": args.trigger,
        }
    elif args.command == "system-state":
        request = {"command": "system_state"}

    result = execute(request, wf_id)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
