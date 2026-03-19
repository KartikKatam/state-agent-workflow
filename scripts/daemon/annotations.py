"""Think annotation lifecycle — emit, validate (synchronous), clear, CHOSEN extraction.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
Chunk 5: validate_think() is now synchronous and blocking (was _async_validate_think).
"""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from schemas.annotation import (
    AnnotationEntry,
    ThinkValidationLogEntry,
    ThinkValidationResult,
)
from schemas.state_machine import StateDefinition, StateMachineDefinition

from scripts.daemon import state_manager as _sm


def _emit_think_annotation(agent_id: str, state_def: StateDefinition) -> None:
    """Write critical annotation to ~/.claude/annotations/{agent-id}.jsonl."""
    _sm.ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    ann_file = _sm.ANNOTATIONS_DIR / f"{agent_id}.jsonl"

    ann_id = "ann-" + hashlib.sha256(
        f"{agent_id}:{state_def.name}:{time.time()}".encode()
    ).hexdigest()[:8]
    entry = AnnotationEntry(
        timestamp=datetime.now(timezone.utc),
        source="state_machine",
        priority="critical",
        target_agent=agent_id,
        message=state_def.think_prompt
        or f"State {state_def.name} requires Think tool deliberation",
        annotation_id=ann_id,
        acknowledged=False,
    )
    with open(ann_file, "a") as f:
        f.write(entry.model_dump_json(exclude_defaults=True, exclude_none=True) + "\n")


def validate_think(
    agent_id: str,
    current_state: str,
    machine: StateMachineDefinition,
    think_output: str,
) -> dict:
    """Validate think output synchronously. BLOCKING — determines annotation clearance.

    Checks: (1) all numbered questions answered, (2) CHOSEN declaration present.
    If complete: returns {"complete": True, "chosen": "VALUE"}.
    If incomplete: returns {"complete": False, "issues": [...], "chosen": None}.

    Also logs to decisions/{agent-id}.jsonl for audit.
    """
    state_def = next(
        (s for s in machine.states if s.name == current_state), None
    )
    if not state_def or not state_def.think_on_exit or not state_def.think_prompt:
        # Not a think state — treat as complete (no validation needed)
        return ThinkValidationResult(complete=True).model_dump()

    # If tool_input provided structured chosen, skip regex parsing
    # (The MCP think tool provides chosen as a separate parameter)
    if "CHOSEN=" in think_output:
        # Quick extraction for MCP-style "Recorded. CHOSEN=VALUE" output
        import re as _re

        quick_match = _re.search(r"CHOSEN=(\w+)", think_output)
        if quick_match:
            return ThinkValidationResult(
                complete=True, chosen=quick_match.group(1)
            ).model_dump()

    issues: list[str] = []

    # Check all numbered questions have answers
    expected = set(re.findall(r"(\d+)\)", state_def.think_prompt))
    answered = set(re.findall(r"(\d+)\)", think_output))
    missing = expected - answered
    if missing:
        issues.append(f"missing_answers: {sorted(missing, key=int)}")

    # Check CHOSEN declaration present
    chosen_match = re.search(r"CHOSEN[:\s]+(\w+)", think_output, re.IGNORECASE)
    if not chosen_match:
        issues.append("missing_chosen")

    chosen = chosen_match.group(1) if chosen_match else None

    # Log to decisions JSONL synchronously
    log_entry = ThinkValidationLogEntry(
        timestamp=datetime.now(timezone.utc),
        agent_id=agent_id,
        state=current_state,
        tool="Think",
        complete=len(issues) == 0,
        issues=issues or None,
        chosen=chosen,
    )
    try:
        decisions_dir = Path.home() / ".claude" / "logs" / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        log_file = decisions_dir / f"{agent_id}.jsonl"
        with open(log_file, "a") as f:
            f.write(
                log_entry.model_dump_json(
                    exclude_defaults=True, exclude_none=True
                )
                + "\n"
            )
    except Exception:
        pass

    result = ThinkValidationResult(
        complete=len(issues) == 0, issues=issues, chosen=chosen
    )
    return result.model_dump()


# Keep old name as alias for backward compat during migration
_async_validate_think = validate_think
