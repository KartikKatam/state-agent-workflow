# Handoff: Think MCP Test Suite

## What Was Built

A purpose-built Think MCP server (`scripts/mcp_think.py`) that replaces the missing native "Think" tool in Claude Code. The daemon and all 8 state machines were updated to use it. Everything is implemented — tests need to be written and iterated until passing.

## Files Modified (context for test writing)

| File | What Changed |
|------|-------------|
| `scripts/mcp_think.py` | **NEW** — Single-tool stateless MCP. `think(thought, chosen)` with enum tree validation, W3C trace logging, minimal returns. |
| `.claude/settings.json` | Added `mcp__think` to permissions, `mcp__think__think` to PostToolUse matcher |
| `scripts/daemon/transitions.py` | `tool.endswith("__think")` match, reads `chosen` from `tool_input` (lines 443-458, 581) |
| `scripts/daemon/annotations.py` | Fast-path for "CHOSEN=VALUE" format in `validate_think()` (lines 64-71) |
| `scripts/daemon/permissions.py` | Allows `__think` through during annotation blocking (line 196) |
| `state-machines/*.json` (all 8) | 31 think_prompts cleaned of format boilerplate |
| `state-machines/auditor-task.json` | v1.3.0 — ambiguity fix (FIXES_VERIFIED), race fix (CRITIQUE_SENT) |
| `state-machines/auditor-phase.json` | v1.4.0 — removed impossible guard, added forced-finalize + forced-handoff |
| `scripts/daemon/guards/mechanical.py` | 3 new guards: `reinvestigation_cycles_exhausted`, `retry_exhausted`, `coder_fixes_not_submitted` |

## Key Design Decisions (needed to write correct tests)

1. **Agent identity**: Read from `CLAUDE_CODE_AGENT_NAME` env var, NOT passed as parameter. Tests must set this env var.
2. **Chosen validation**: 16 valid values in `ALL_VALID_CHOSEN`. Case-insensitive (normalized to uppercase). Empty string = pacing think.
3. **Tool name in hooks**: Appears as `mcp__think__think`. Daemon matches via `tool.endswith("__think")`.
4. **Decision logs**: Written to `~/.claude/logs/decisions/{agent_id}.jsonl`. Contain `thought_length` but NOT thought content.
5. **Trace context**: Read from `CLAUDE_TRACE_ID`/`CLAUDE_SPAN_ID` env vars, fallback to `~/.claude/state/traces/{agent_id}.json`.
6. **Daemon flow**: PostToolUse → `handle_post_tool()` → if `tool.endswith("__think")`: read `chosen` from `tool_input`, override `validate_think` result → store `last_think_chosen` + `last_think_state` in agent state → clear annotation → auto-transition fires.

## Tests to Write

### File: `tests/test_mcp_think.py` — Unit Tests (9)

Test the MCP tool function directly by importing `from scripts.mcp_think import think, ALL_VALID_CHOSEN, CHOSEN_TO_CATEGORY, DECISION_TREE`.

```python
# Set CLAUDE_CODE_AGENT_NAME in a fixture for all tests
@pytest.fixture(autouse=True)
def set_agent_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_AGENT_NAME", "test-agent")
    # Redirect decision logs to tmp_path to avoid polluting real logs
    import scripts.mcp_think as mcp
    monkeypatch.setattr(mcp, "_DECISIONS_DIR", tmp_path / "decisions")
```

1. **`test_think_valid_chosen`** — Call `think("reasoning", chosen)` for EACH of the 16 values in `ALL_VALID_CHOSEN`. Assert returns `f"Recorded. CHOSEN={value}"`.
2. **`test_think_empty_chosen`** — `think("reasoning", "")` returns `"Recorded."` (no CHOSEN in output).
3. **`test_think_invalid_chosen`** — `think("reasoning", "BANANA")` returns string containing `"Invalid"` and lists valid values.
4. **`test_think_case_normalization`** — `think("reasoning", "approve")` returns `"Recorded. CHOSEN=APPROVE"`.
5. **`test_think_whitespace_handling`** — `think("reasoning", "  APPROVE  ")` returns `"Recorded. CHOSEN=APPROVE"`.
6. **`test_decision_log_written`** — After `think("reasoning", "APPROVE")`, read the JSONL file. Verify entry has `timestamp`, `agent_id`, `chosen`, `thought_length`, `decision_category`, `think_span_id`.
7. **`test_decision_log_trace_context`** — Set `CLAUDE_TRACE_ID` and `CLAUDE_SPAN_ID` env vars. Call think. Verify log entry has `trace_id`, `span_id`, `parent_span_id`.
8. **`test_decision_log_no_thought_content`** — Call `think("secret reasoning text", "APPROVE")`. Read log. Assert `"secret reasoning text"` does NOT appear anywhere in the log entry. Only `thought_length` (length of the string) is present.
9. **`test_category_mapping`** — Verify `CHOSEN_TO_CATEGORY` maps: APPROVE→review, CRITIQUE→review, FINALIZE→investigation, REINVESTIGATE→investigation, RETRY→recovery, HANDOFF→recovery, ABORT→recovery, REUSE→exploration, EXPLORE→exploration, DISPATCH→exploration, CLARIFY→exploration, RELATED→relatedness, UNRELATED→relatedness, FALLBACK→strategy, SYNTHESIZE→synthesis, PARTIAL_SYNTHESIS→synthesis.

### File: `tests/test_daemon_think_integration.py` — Integration Tests (8)

These test the daemon's `handle_post_tool` function with Think MCP tool inputs. You'll need to set up agent state files and mock the daemon's state management.

**Key imports:**
```python
from scripts.daemon.transitions import handle_post_tool, _try_auto_transition
from scripts.daemon.annotations import validate_think
from scripts.daemon.guards.decision import _guard_think_chosen, _guard_think_completed
```

**Setup**: Create a temporary agent state file and load an auditor-task state machine. The daemon reads agent state from `~/.claude/state/agents/{agent_id}.json` (see `scripts/daemon/state_manager.py` for paths).

10. **`test_daemon_reads_chosen_from_tool_input`** — Call `handle_post_tool(agent_id, "mcp__think__think", {"thought": "...", "chosen": "APPROVE"}, "Recorded. CHOSEN=APPROVE")`. Verify the returned result processes correctly (no error in feedback).
11. **`test_daemon_clears_annotation_on_valid_think`** — Set `pending_critical_annotation` in agent state. Call handle_post_tool with a valid think. Verify `pending_critical_annotation` is cleared in agent state file.
12. **`test_daemon_stores_last_think_chosen`** — After handle_post_tool with chosen="APPROVE", read agent state. Verify `last_think_chosen == "APPROVE"` and `last_think_state == current_state`.
13. **`test_daemon_auto_transition_after_think`** — Set up agent in CODE_REVIEW state with auditor-task SM. Think with chosen=APPROVE. Verify auto-transition fired: agent state now in APPROVED.
14. **`test_daemon_pacing_think_completed`** — Set up agent in ARTIFACT_REVIEW state with auditor-phase SM. Think with chosen="". Verify `think_completed` guard passes and agent transitions to INDEPENDENT_EXPLORATION.
15. **`test_daemon_allows_think_during_annotation_blocking`** — Import permissions check. Verify `mcp__think__think` is allowed when `pending_critical_annotation` is set. Verify `Write` is blocked in same state.
16. **`test_daemon_rejects_mutations_until_think`** — Set `pending_critical_annotation` in agent state. Call pre_tool check for Write → blocked. Call handle_post_tool with valid think → annotation cleared. Call pre_tool check for Write → allowed.
17. **`test_validate_think_mcp_fast_path`** — Call `validate_think(agent_id, "CODE_REVIEW", machine, "Recorded. CHOSEN=APPROVE")`. Verify returns `{"complete": True, "chosen": "APPROVE"}` without hitting the regex path.

### File: `tests/test_auditor_think_e2e.py` — End-to-End Tests (5)

These simulate full state machine paths. Set up agent state, fire transitions with think calls, verify terminal states.

18. **`test_auditor_task_approve_flow`** — Register agent in SPAWNED. Set mechanical guards (coder_files_readable, etc.) to pass. Transition to CODE_REVIEW. Think(chosen=APPROVE). Verify agent reaches APPROVED terminal state.
19. **`test_auditor_task_critique_escalation`** — CODE_REVIEW → think(CRITIQUE) → CRITIQUE_SENT. Set coder_notified_fixes_ready. Transition to FIXES_VERIFIED. Repeat think(CRITIQUE) 3 times through the cycle. On 4th think(CRITIQUE) from FIXES_VERIFIED, verify agent reaches ESCALATED (via critique_cycles_exceeded + think_chosen:CRITIQUE).
20. **`test_auditor_phase_finalize_flow`** — RULING → think(FINALIZE) → REPORT_WRITING. Verify transition fires without the old `all_adherence_rulings_issued` guard blocking it.
21. **`test_auditor_phase_reinvestigation_exhausted`** — RULING → think(REINVESTIGATE) → INDEPENDENT_EXPLORATION → think(any) → RULING. Repeat 2x. On 3rd think(REINVESTIGATE) from RULING, verify agent reaches REPORT_WRITING via `reinvestigation_cycles_exhausted` guard.
22. **`test_auditor_phase_error_retry_exhausted`** — ERROR → think(RETRY) → CONTEXT_LOADING → (fail) → ERROR. On 2nd think(RETRY), verify agent reaches HANDOFF via `retry_exhausted` guard.

## How to Run

```bash
# Unit tests only (fast, no daemon needed)
pytest tests/test_mcp_think.py -v

# Integration tests (need daemon state setup)
pytest tests/test_daemon_think_integration.py -v

# E2E tests (full state machine simulation)
pytest tests/test_auditor_think_e2e.py -v

# All together
pytest tests/test_mcp_think.py tests/test_daemon_think_integration.py tests/test_auditor_think_e2e.py -v
```

## Important: Daemon State Management

The daemon uses these paths (see `scripts/daemon/state_manager.py`):
- Agent state: `~/.claude/state/agents/{agent_id}.json`
- Transition log: `~/.claude/logs/state-transitions.jsonl`
- Annotations: `~/.claude/annotations/{agent_id}.jsonl`
- State machines: loaded from `state-machines/{name}.json` in project dir

For tests, you'll need to either:
- Use `monkeypatch` / `tmp_path` to redirect these paths
- Or use the daemon's own setup utilities if they exist

Check `scripts/daemon/state_manager.py` for the exact path constants and helper functions (`get_agent_state`, `save_agent_state`, `load_machine`, etc.).

## Quality Gate

```bash
./scripts/gate.sh
```
Components: ruff format, ruff check, pyright, pytest. All tests must pass the gate.

## What NOT to Change

- Do not modify `scripts/mcp_think.py` unless tests reveal a genuine bug
- Do not modify state machine files unless a test reveals a transition issue
- Do not modify the daemon files unless a test reveals integration bugs
- Focus on writing tests that verify the existing implementation works correctly
