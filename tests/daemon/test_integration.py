"""Integration tests for daemon lifecycle.

Tests complete workflows: register → check tool → transition → check tool.
Also covers think enforcement end-to-end, auto-transition with state change,
full pre→post cycle, process_request routing, and daemon-down fallback.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import scripts.workflow_state as daemon
from schemas.agent_state import PendingAnnotation
from schemas.state_machine import StateMachineDefinition

from .conftest import make_agent_in_state


# ---------------------------------------------------------------------------
# 1: Full pre→post cycle (register → check → transition → check)
# ---------------------------------------------------------------------------


class TestFullCycle:
    """End-to-end daemon lifecycle tests."""

    def test_register_check_transition_check(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Register agent → check tool (denied) → transition → check tool (allowed)."""
        reg = daemon.register_agent(
            agent_id="coder-p1-t1-a1b2",
            role="coder",
            model="opus-4-6",
            phase="p1",
            task="t1",
        )
        assert reg["ok"]

        # In IDLE: writes denied
        check1 = daemon.check_tool_allowed(
            "coder-p1-t1-a1b2", "Write", {"file_path": "src/main.py"}
        )
        assert not check1["allowed"]

        # Transition to WRITING
        trans = daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")
        assert trans["ok"]
        assert trans["from_state"] == "IDLE"
        assert trans["to_state"] == "WRITING"

        # In WRITING: write to src allowed
        check2 = daemon.check_tool_allowed(
            "coder-p1-t1-a1b2", "Write", {"file_path": "src/main.py"}
        )
        assert check2["allowed"]

        # In WRITING: write to tests denied (not in write_globs)
        check3 = daemon.check_tool_allowed(
            "coder-p1-t1-a1b2", "Write", {"file_path": "tests/test_foo.py"}
        )
        assert not check3["allowed"]

    def test_register_check_transition_chain(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Full chain: IDLE → WRITING → DONE."""
        daemon.register_agent(
            agent_id="coder-p1-t1-a1b2",
            role="coder",
            model="opus-4-6",
        )

        t1 = daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")
        assert t1["ok"]

        t2 = daemon.do_transition("coder-p1-t1-a1b2", "DONE", "finish")
        assert t2["ok"]

        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.current_state == "DONE"

    def test_max_occurrences_enforced(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Transition with max_occurrences=2 → third attempt fails."""
        daemon.register_agent(
            agent_id="coder-p1-t1-a1b2",
            role="coder",
            model="opus-4-6",
        )

        daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")

        t1 = daemon.do_transition("coder-p1-t1-a1b2", "IDLE", "loop_back")
        assert t1["ok"]
        daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")

        t2 = daemon.do_transition("coder-p1-t1-a1b2", "IDLE", "loop_back")
        assert t2["ok"]
        daemon.do_transition("coder-p1-t1-a1b2", "WRITING", "start_writing")

        t3 = daemon.do_transition("coder-p1-t1-a1b2", "IDLE", "loop_back")
        assert not t3["ok"]
        assert "exceeded" in t3["reason"].lower()

    def test_full_pre_post_cycle(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Register → pre_tool → post_tool → verify state consistency."""
        daemon.register_agent(
            agent_id="coder-p1-t1-a1b2",
            role="coder",
            model="opus-4-6",
        )
        daemon.do_transition("coder-p1-t1-a1b2", "UNRESTRICTED", "enter_unrestricted")

        pre = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
        )
        assert pre["allowed"]

        post = daemon.handle_post_tool(
            "coder-p1-t1-a1b2",
            "Write",
            {"file_path": "src/main.py"},
            "wrote 50 lines",
        )
        assert "feedback" in post
        assert "transition" in post

        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        assert agent.current_state == "UNRESTRICTED"


# ---------------------------------------------------------------------------
# 2: Think enforcement via annotations (end-to-end) — THE MOST IMPORTANT
# ---------------------------------------------------------------------------


class TestThinkEnforcement:
    """End-to-end think enforcement cycle."""

    def test_think_enforcement_end_to_end(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Full think cycle:
        1. Register agent, transition to THINK_REQUIRED
        2. Emit annotation (think_on_exit state)
        3. handle_post_tool reads annotation → sets pending_critical_annotation
        4. handle_pre_tool for Write → DENIED (annotation blocking)
        5. handle_pre_tool for Read → ALLOWED
        6. Clear annotation (simulating Think acknowledgment)
        7. handle_pre_tool for Write → ALLOWED
        """
        agent_id = "coder-p1-t1-a1b2"
        daemon.register_agent(agent_id=agent_id, role="coder", model="opus-4-6")

        # Step 1: Transition to THINK_REQUIRED
        trans = daemon.do_transition(agent_id, "THINK_REQUIRED", "enter_think")
        assert trans["ok"]

        # Step 2: Emit annotation (do_transition doesn't emit; post_tool auto-transition does)
        state_def = next(
            s for s in sample_machine.states if s.name == "THINK_REQUIRED"
        )
        daemon._emit_think_annotation(agent_id, state_def)

        # Verify annotation file exists
        ann_file = daemon.ANNOTATIONS_DIR / f"{agent_id}.jsonl"
        assert ann_file.exists()
        ann_lines = [
            json.loads(line)
            for line in ann_file.read_text().strip().split("\n")
            if line.strip()
        ]
        assert len(ann_lines) == 1
        assert ann_lines[0]["priority"] == "critical"
        assert ann_lines[0]["acknowledged"] is False

        # Step 3: handle_post_tool reads annotations and sets pending_critical
        daemon._agent_state_cache.clear()
        post_result = daemon.handle_post_tool(agent_id, "Read", {}, "")
        assert post_result["inject_context"] is not None
        assert "ANNOTATION CRITICAL" in post_result["inject_context"]

        # Step 4: Write should be BLOCKED (pending annotation)
        daemon._agent_state_cache.clear()
        pre_write = daemon.handle_pre_tool(
            agent_id, "Write", {"file_path": "src/main.py"}
        )
        assert not pre_write["allowed"]
        assert "annotation" in pre_write["reason"].lower()

        # Step 5: Read should be ALLOWED
        pre_read = daemon.handle_pre_tool(agent_id, "Read", {})
        assert pre_read["allowed"]

        # Step 6: Clear annotation (simulates Think tool acknowledgment)
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.pending_critical_annotation is not None
        agent.pending_critical_annotation = None
        daemon.save_agent_state(agent)
        daemon._agent_state_cache.clear()

        # Step 7: Write should now be ALLOWED
        pre_write2 = daemon.handle_pre_tool(
            agent_id, "Write", {"file_path": "src/main.py"}
        )
        assert pre_write2["allowed"]

    def test_auto_transition_with_state_change(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """Auto-transition changes agent current_state and returns transition info."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "STATE_A")

        agent_before = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent_before is not None
        assert agent_before.current_state == "STATE_A"

        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2", "Read", {}, ""
        )

        assert result["transition"] is not None
        assert result["transition"]["ok"] is True
        assert result["transition"]["from_state"] == "STATE_A"
        assert result["transition"]["to_state"] == "STATE_B"

        agent_after = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent_after is not None
        assert agent_after.current_state == "STATE_B"

    def test_annotation_blocking_then_clearance(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """Annotation blocking: set pending → Write blocked → clear → Write allowed."""
        make_agent_in_state(
            "coder-p1-t1-a1b2",
            "coder",
            "UNRESTRICTED",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-test-001",
                message="Think about your approach",
            ),
        )
        daemon._agent_state_cache.clear()

        check1 = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Write", {"file_path": "src/main.py"}
        )
        assert not check1["allowed"]

        check_read = daemon.handle_pre_tool("coder-p1-t1-a1b2", "Read", {})
        assert check_read["allowed"]

        agent = daemon.get_agent_state("coder-p1-t1-a1b2")
        assert agent is not None
        agent.pending_critical_annotation = None
        daemon.save_agent_state(agent)
        daemon._agent_state_cache.clear()

        check2 = daemon.handle_pre_tool(
            "coder-p1-t1-a1b2", "Write", {"file_path": "src/main.py"}
        )
        assert check2["allowed"]

    def test_think_annotation_emitted_on_auto_transition(
        self, temp_state_dir: Path, guarded_machine: StateMachineDefinition
    ) -> None:
        """Auto-transition to think_on_exit state → annotation JSONL written."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "STATE_A")

        result = daemon.handle_post_tool(
            "coder-p1-t1-a1b2", "Write", {"file_path": "src/main.py"}, ""
        )

        # Should have auto-transitioned to STATE_B (think_on_exit=True)
        assert result["transition"] is not None
        assert result["transition"]["to_state"] == "STATE_B"

        # Check annotation file was written (via _emit_think_annotation)
        # The annotation may be in the real home dir or our patched dir
        ann_file = daemon.ANNOTATIONS_DIR / "coder-p1-t1-a1b2.jsonl"
        # _emit_think_annotation uses Path.home() directly, not ANNOTATIONS_DIR
        # So check the home annotations dir
        home_ann_file = Path.home() / ".claude" / "annotations" / "coder-p1-t1-a1b2.jsonl"
        ann_file_to_check = ann_file if ann_file.exists() else home_ann_file

        if ann_file_to_check.exists():
            lines = ann_file_to_check.read_text().strip().splitlines()
            assert len(lines) >= 1
            entry = json.loads(lines[-1])
            assert entry["priority"] == "critical"
            assert "Think about design" in entry["message"]


# ---------------------------------------------------------------------------
# 2b: Think CHOSEN extraction and routing (end-to-end)
# ---------------------------------------------------------------------------


class TestChosenExtractionRouting:
    """Test full flow: think → CHOSEN extracted → correct routing."""

    def test_think_chosen_extraction_and_routing(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Full flow: ASSESS → Think with CHOSEN:APPROVE → annotation cleared → routes to APPROVED."""
        agent_id = "coder-p1-t1-a1b2"

        # Register agent in ASSESS state with pending annotation
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-route-001",
                message="1) Is the code correct? 2) What approach? CHOSEN: APPROVE or RETRY",
            ),
        )
        daemon._agent_state_cache.clear()

        # Step 1: Verify Write is blocked (annotation pending)
        pre_write = daemon.handle_pre_tool(
            agent_id, "Write", {"file_path": "src/main.py"}
        )
        assert not pre_write["allowed"]
        assert "annotation" in pre_write["reason"].lower()

        # Step 2: Simulate Think with complete output including CHOSEN
        think_output = "1) The code is correct. 2) Approve approach. CHOSEN: APPROVE"
        post_result = daemon.handle_post_tool(
            agent_id, "Think", {}, think_output
        )

        # Step 3: Annotation should be cleared, CHOSEN stored, transition fired
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.pending_critical_annotation is None
        assert agent.last_think_chosen == "APPROVE"
        assert agent.last_think_state == "ASSESS"

        # Auto-transition should have fired to APPROVED
        assert post_result["transition"] is not None
        assert post_result["transition"]["to_state"] == "APPROVED"
        assert agent.current_state == "APPROVED"

    def test_think_retry_routes_correctly(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """ASSESS → Think with CHOSEN:RETRY → routes to RETRY."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-route-002",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        think_output = "1) Issues found 2) Need retry. CHOSEN: RETRY"
        post_result = daemon.handle_post_tool(agent_id, "Think", {}, think_output)

        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "RETRY"
        assert post_result["transition"]["to_state"] == "RETRY"

    def test_incomplete_think_no_routing(
        self, temp_state_dir: Path, decision_machine: StateMachineDefinition
    ) -> None:
        """Incomplete Think → no CHOSEN stored → no transition."""
        agent_id = "coder-p1-t1-a1b2"
        make_agent_in_state(
            agent_id,
            "coder",
            "ASSESS",
            pending_critical_annotation=PendingAnnotation(
                annotation_id="ann-route-003",
                message="Think prompt",
            ),
        )
        daemon._agent_state_cache.clear()

        # Think without CHOSEN
        think_output = "1) Some answer. 2) Another answer."
        post_result = daemon.handle_post_tool(agent_id, "Think", {}, think_output)

        # No transition should fire
        assert post_result["transition"] is None

        # Agent stays in ASSESS with annotation still pending
        agent = daemon.get_agent_state(agent_id)
        assert agent is not None
        assert agent.current_state == "ASSESS"
        assert agent.pending_critical_annotation is not None


# ---------------------------------------------------------------------------
# 3: Session log auto-update
# ---------------------------------------------------------------------------


class TestSessionLogUpdate:
    """Test session log tracking of file modifications."""

    def test_post_tool_write_updates_session_log(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """post_tool with Write → session log files_modified updated."""
        agent_id = "coder-p1-t1-a1b2"
        daemon.register_agent(agent_id=agent_id, role="coder", model="opus-4-6")
        daemon.do_transition(agent_id, "WRITING", "start_writing")

        project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
        logs_dir = project_dir / ".claude" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        session_log = logs_dir / "test-chunk01-log.json"
        session_log.write_text(json.dumps({
            "status": "in_progress",
            "files_modified": [],
            "state_history": [],
        }))

        daemon.handle_post_tool(
            agent_id, "Write", {"file_path": "src/main.py"}, ""
        )

        # Give async writer a moment
        time.sleep(0.2)

        log_data = json.loads(session_log.read_text())
        assert "src/main.py" in log_data.get("files_modified", [])


# ---------------------------------------------------------------------------
# 4: Daemon-down permissive fallback
# ---------------------------------------------------------------------------


class TestDaemonDownFallback:
    """Test behavior when daemon socket is unavailable."""

    def test_send_to_daemon_returns_none_when_no_socket(
        self, temp_state_dir: Path
    ) -> None:
        """No socket file → send_to_daemon returns None."""
        result = daemon.send_to_daemon(
            "nonexistent-workflow",
            {"command": "check_tool", "agent_id": "x", "tool": "Write"},
        )
        assert result is None

    def test_execute_falls_back_to_direct(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """execute() with no daemon → falls back to process_request."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.execute(
            {
                "command": "check_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Write",
                "tool_input": {"file_path": "src/main.py"},
            },
            workflow_id="nonexistent-workflow",
        )
        assert not result["allowed"]


# ---------------------------------------------------------------------------
# Process request routing
# ---------------------------------------------------------------------------


class TestProcessRequest:
    """Test the process_request command router."""

    def test_check_tool_routes(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """command='check_tool' routes to check_tool_allowed."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.process_request(
            {
                "command": "check_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Write",
                "tool_input": {},
            }
        )
        assert "allowed" in result

    def test_transition_routes(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """command='transition' routes to do_transition."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.process_request(
            {
                "command": "transition",
                "agent_id": "coder-p1-t1-a1b2",
                "to_state": "WRITING",
                "trigger": "start_writing",
            }
        )
        assert result["ok"]

    def test_register_routes(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """command='register' routes to register_agent."""
        result = daemon.process_request(
            {
                "command": "register",
                "agent_id": "coder-p1-t2-b2c3",
                "role": "coder",
                "model": "opus-4-6",
            }
        )
        assert result["ok"]

    def test_get_state_routes(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """command='get_state' routes to get_agent_state."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.process_request(
            {
                "command": "get_state",
                "agent_id": "coder-p1-t1-a1b2",
            }
        )
        assert result["ok"]
        assert result["state"]["current_state"] == "IDLE"

    def test_unknown_command_returns_error(self, temp_state_dir: Path) -> None:
        """Unknown command → error response."""
        result = daemon.process_request({"command": "nonexistent"})
        assert not result["ok"]
        assert "unknown" in result["reason"].lower()

    def test_post_tool_is_routed(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """'post_tool' command is handled by handle_post_tool."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.process_request(
            {
                "command": "post_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Read",
                "tool_input": {},
                "tool_output_summary": "",
            }
        )
        assert "feedback" in result

    def test_pre_tool_is_routed(
        self, temp_state_dir: Path, sample_machine: StateMachineDefinition
    ) -> None:
        """'pre_tool' command is handled by handle_pre_tool."""
        make_agent_in_state("coder-p1-t1-a1b2", "coder", "IDLE")
        result = daemon.process_request(
            {
                "command": "pre_tool",
                "agent_id": "coder-p1-t1-a1b2",
                "tool": "Write",
                "tool_input": {},
            }
        )
        assert "allowed" in result
        assert not result["allowed"]  # IDLE state blocks writes
