"""Unit tests for the Think MCP server (scripts/mcp_think.py).

Tests cover:
  - Valid chosen values (all 16)
  - Empty chosen (pacing think)
  - Invalid chosen rejection
  - Case normalization and whitespace handling
  - Decision log structure and privacy
  - Category mapping correctness
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.mcp_think as mcp
from scripts.mcp_think import (
    ALL_VALID_CHOSEN,
    CHOSEN_TO_CATEGORY,
    think,
)


@pytest.fixture(autouse=True)
def _set_agent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Set agent identity and redirect decision logs to tmp_path."""
    monkeypatch.setenv("CLAUDE_CODE_AGENT_NAME", "test-agent")
    # Clear trace env vars to avoid interference
    monkeypatch.delenv("CLAUDE_TRACE_ID", raising=False)
    monkeypatch.delenv("CLAUDE_SPAN_ID", raising=False)
    monkeypatch.delenv("CLAUDE_PARENT_SPAN_ID", raising=False)
    # Redirect decision logs to tmp_path
    monkeypatch.setattr(mcp, "_DECISIONS_DIR", tmp_path / "decisions")


# ---------------------------------------------------------------------------
# 1. Valid chosen values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chosen", sorted(ALL_VALID_CHOSEN))
def test_think_valid_chosen(chosen: str) -> None:
    """Each of the 16 valid CHOSEN values returns 'Recorded. CHOSEN=VALUE'."""
    result = think("reasoning about something", chosen)
    assert result == f"Recorded. CHOSEN={chosen}"


# ---------------------------------------------------------------------------
# 2. Empty chosen (pacing think)
# ---------------------------------------------------------------------------


def test_think_empty_chosen() -> None:
    """Empty chosen returns 'Recorded.' without CHOSEN in output."""
    result = think("just reflecting", "")
    assert result == "Recorded."


# ---------------------------------------------------------------------------
# 3. Invalid chosen
# ---------------------------------------------------------------------------


def test_think_invalid_chosen() -> None:
    """Invalid chosen returns error message listing valid values."""
    result = think("reasoning", "BANANA")
    assert "Invalid" in result
    # Should list at least some valid values
    assert "APPROVE" in result


# ---------------------------------------------------------------------------
# 4. Case normalization
# ---------------------------------------------------------------------------


def test_think_case_normalization() -> None:
    """Lowercase chosen is normalized to uppercase."""
    result = think("reasoning", "approve")
    assert result == "Recorded. CHOSEN=APPROVE"


# ---------------------------------------------------------------------------
# 5. Whitespace handling
# ---------------------------------------------------------------------------


def test_think_whitespace_handling() -> None:
    """Leading/trailing whitespace in chosen is stripped."""
    result = think("reasoning", "  APPROVE  ")
    assert result == "Recorded. CHOSEN=APPROVE"


# ---------------------------------------------------------------------------
# 6. Decision log written
# ---------------------------------------------------------------------------


def test_decision_log_written() -> None:
    """After think(), decision log JSONL file exists with correct fields."""
    think("reasoning about approval", "APPROVE")

    log_dir = mcp._DECISIONS_DIR
    log_file = log_dir / "test-agent.jsonl"
    assert log_file.exists(), f"Decision log not found at {log_file}"

    lines = log_file.read_text().strip().splitlines()
    assert len(lines) >= 1
    entry = json.loads(lines[-1])

    assert entry["agent_id"] == "test-agent"
    assert entry["chosen"] == "APPROVE"
    assert entry["thought"] == "reasoning about approval"
    assert entry["thought_length"] == len("reasoning about approval")
    assert entry["decision_category"] == "review"
    assert "timestamp" in entry
    assert "think_span_id" in entry


# ---------------------------------------------------------------------------
# 7. Decision log trace context
# ---------------------------------------------------------------------------


def test_decision_log_trace_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """When trace env vars are set, log entry includes trace context."""
    monkeypatch.setenv("CLAUDE_TRACE_ID", "trace-abc123")
    monkeypatch.setenv("CLAUDE_SPAN_ID", "span-def456")

    think("reasoning", "APPROVE")

    log_file = mcp._DECISIONS_DIR / "test-agent.jsonl"
    entry = json.loads(log_file.read_text().strip().splitlines()[-1])

    assert entry["trace_id"] == "trace-abc123"
    # span_id is the think_span_id (newly generated), not the parent
    assert "span_id" in entry
    assert entry["parent_span_id"] == "span-def456"


# ---------------------------------------------------------------------------
# 8. Decision log includes thought content
# ---------------------------------------------------------------------------


def test_decision_log_includes_thought() -> None:
    """Full thought content is logged for post-hoc traceability."""
    reasoning = "The implementation matches the plan because X, Y, Z."
    think(reasoning, "APPROVE")

    log_file = mcp._DECISIONS_DIR / "test-agent.jsonl"
    entry = json.loads(log_file.read_text().strip().splitlines()[-1])

    assert entry["thought"] == reasoning
    assert entry["thought_length"] == len(reasoning)


# ---------------------------------------------------------------------------
# 9. Category mapping
# ---------------------------------------------------------------------------


def test_category_mapping() -> None:
    """Verify CHOSEN_TO_CATEGORY maps all 16 values to correct categories."""
    expected = {
        "APPROVE": "review",
        "CRITIQUE": "review",
        "FINALIZE": "investigation",
        "REINVESTIGATE": "investigation",
        "RETRY": "recovery",
        "HANDOFF": "recovery",
        "ABORT": "recovery",
        "REUSE": "exploration",
        "EXPLORE": "exploration",
        "DISPATCH": "exploration",
        "CLARIFY": "exploration",
        "RELATED": "relatedness",
        "UNRELATED": "relatedness",
        "FALLBACK": "strategy",
        "SYNTHESIZE": "synthesis",
        "PARTIAL_SYNTHESIS": "synthesis",
    }
    assert CHOSEN_TO_CATEGORY == expected
    assert len(ALL_VALID_CHOSEN) == 16
