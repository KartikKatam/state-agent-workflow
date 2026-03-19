#!/usr/bin/env python3
"""
Schema Validator Utility

Validates .claude/ JSON files against Pydantic models.
Maps file paths to schema types, then validates via model_validate_json().

Graceful degradation: if a schema module hasn't been written yet,
the validator allows the file through rather than crashing.
"""

from __future__ import annotations

import json
import os
from typing import Any

# ---------------------------------------------------------------------------
# Schema type → Pydantic model registry
#
# Populated at import time via try/except. Missing schemas are skipped —
# files of those types pass through unvalidated until the schema lands.
# ---------------------------------------------------------------------------

_SCHEMA_REGISTRY: dict[str, Any] = {}

# --- Foundation schemas (task #2) ---
try:
    from schemas.agent_state import AgentState

    _SCHEMA_REGISTRY["agent-state"] = AgentState
except ImportError:
    pass

try:
    from schemas.system_state import WorkflowState

    _SCHEMA_REGISTRY["system-state"] = WorkflowState
except ImportError:
    pass

# --- Logging & observability schemas (task #3) ---
try:
    from schemas.state_transition_log import StateTransitionEntry

    _SCHEMA_REGISTRY["state-transition-log"] = StateTransitionEntry
except ImportError:
    pass

try:
    from schemas.decision_log import DecisionLogEntry

    _SCHEMA_REGISTRY["decision-log"] = DecisionLogEntry
except ImportError:
    pass

try:
    from schemas.message_bus_log import MessageBusLogEntry

    _SCHEMA_REGISTRY["message-bus-log"] = MessageBusLogEntry
except ImportError:
    pass

# --- Communication schemas (task #4) ---
# AgentMessage is a discriminated union type alias, not a BaseModel.
# We store its TypeAdapter so validate_json() works the same way.
try:
    from pydantic import TypeAdapter

    from schemas.message_protocol import AgentMessage

    _SCHEMA_REGISTRY["message-protocol"] = TypeAdapter(AgentMessage)
except ImportError:
    pass

try:
    from schemas.annotation import AnnotationEntry

    _SCHEMA_REGISTRY["annotation"] = AnnotationEntry
except ImportError:
    pass

# --- Context & research schemas (task #8) ---
try:
    from schemas.context_packet_codebase import ContextPacketCodebase

    _SCHEMA_REGISTRY["context-packet-codebase"] = ContextPacketCodebase
except ImportError:
    pass

try:
    from schemas.context_packet_feature import ContextPacketFeature

    _SCHEMA_REGISTRY["context-packet-feature"] = ContextPacketFeature
except ImportError:
    pass

try:
    from schemas.context_packet_query import ContextPacketQuery

    _SCHEMA_REGISTRY["context-packet-query"] = ContextPacketQuery
except ImportError:
    pass

try:
    from schemas.research_output import ResearchOutput

    _SCHEMA_REGISTRY["research-output"] = ResearchOutput
except ImportError:
    pass

try:
    from schemas.research_index import ResearchIndex

    _SCHEMA_REGISTRY["research-index"] = ResearchIndex
except ImportError:
    pass

# --- Planning schemas (task #9) ---
try:
    from schemas.design_document import DesignDocument

    _SCHEMA_REGISTRY["design-document"] = DesignDocument
except ImportError:
    pass

try:
    from schemas.plan import Plan

    _SCHEMA_REGISTRY["plan"] = Plan
except ImportError:
    pass

try:
    from schemas.planning_log import PlanningLog

    _SCHEMA_REGISTRY["planning-log"] = PlanningLog
except ImportError:
    pass

# --- Implementation schemas (task #10) ---
try:
    from schemas.session_log import SessionLog

    _SCHEMA_REGISTRY["session-log"] = SessionLog
except ImportError:
    pass

try:
    from schemas.handoff import Handoff

    _SCHEMA_REGISTRY["handoff"] = Handoff
except ImportError:
    pass

try:
    from schemas.quality_gate_result import QualityGateResult

    _SCHEMA_REGISTRY["quality-gate-result"] = QualityGateResult
except ImportError:
    pass

# --- Testing & auditing schemas (task #11) ---
try:
    from schemas.test_plan import TestPlan

    _SCHEMA_REGISTRY["test-plan"] = TestPlan
except ImportError:
    pass

try:
    from schemas.scenario_result import ScenarioResult

    _SCHEMA_REGISTRY["scenario-result"] = ScenarioResult
except ImportError:
    pass

try:
    from schemas.audit_report import AuditReport

    _SCHEMA_REGISTRY["audit-report"] = AuditReport
except ImportError:
    pass

try:
    from schemas.phase_report import PhaseReport

    _SCHEMA_REGISTRY["phase-report"] = PhaseReport
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Human-readable labels for error messages
# ---------------------------------------------------------------------------

SCHEMA_LABELS: dict[str, str] = {
    "agent-state": "agent state",
    "system-state": "system state",
    "state-transition-log": "state transition log",
    "decision-log": "decision log",
    "message-bus-log": "message bus log",
    "message-protocol": "message protocol",
    "annotation": "annotation",
    "context-packet-codebase": "codebase context packet",
    "context-packet-feature": "feature context packet",
    "context-packet-query": "query context packet",
    "research-output": "research output",
    "research-index": "research index",
    "design-document": "design document",
    "plan": "implementation plan",
    "planning-log": "planning log",
    "session-log": "session log",
    "handoff": "handoff",
    "quality-gate-result": "quality gate result",
    "test-plan": "test plan",
    "scenario-result": "scenario result",
    "audit-report": "audit report",
    "phase-report": "phase report",
}


# ---------------------------------------------------------------------------
# Path → schema type matching
#
# Checked in order. Exact matches first, then directory+suffix patterns.
# Paths are relative to ~/.claude/ or $PROJECT_DIR/.claude/
# ---------------------------------------------------------------------------

# (relative_path, schema_key)
_EXACT_MATCHES: list[tuple[str, str]] = [
    (".claude/context/_codebase.json", "context-packet-codebase"),
    (".claude/research/_index.json", "research-index"),
    (".claude/state/system-state.json", "system-state"),
]

# (dir_prefix, suffix, schema_key)
_SUFFIX_MATCHES: list[tuple[str, str, str]] = [
    # Agent state
    (".claude/state/agents/", ".json", "agent-state"),
    # Context packets
    (".claude/context/queries/", ".json", "context-packet-query"),
    (".claude/context/", "-context.json", "context-packet-feature"),
    # Plans & designs
    (".claude/designs/", ".md", "design-document"),
    (".claude/plans/", "-plan.json", "plan"),
    # Logs
    (".claude/logs/state-transitions", ".jsonl", "state-transition-log"),
    (".claude/logs/decisions/", ".jsonl", "decision-log"),
    (".claude/logs/message-bus", ".jsonl", "message-bus-log"),
    (".claude/logs/", "-planning-log.json", "planning-log"),
    (".claude/logs/", "-log.json", "session-log"),
    # Research
    (".claude/research/", ".json", "research-output"),
    # Handoffs
    (".claude/handoffs/", ".json", "handoff"),
    # Annotations
    (".claude/annotations/", ".jsonl", "annotation"),
    # Testing & auditing
    (".claude/test-plans/", ".json", "test-plan"),
    (".claude/scenarios/", ".json", "scenario-result"),
    (".claude/audits/", ".json", "audit-report"),
    (".claude/reports/", ".json", "phase-report"),
    # Quality gates
    (".claude/quality-gates/", ".json", "quality-gate-result"),
]


def _normalize_path(file_path: str) -> str:
    """Normalize to a relative path containing .claude/ for matching."""
    if os.path.isabs(file_path):
        # Find .claude/ in the path and return from there
        idx = file_path.find("/.claude/")
        if idx != -1:
            return file_path[idx + 1 :]  # Strip leading /
        try:
            return os.path.relpath(file_path).replace("\\", "/")
        except ValueError:
            return file_path.replace("\\", "/")
    return file_path.replace("\\", "/")


def match_schema_type(file_path: str) -> str | None:
    """
    Match a file path to its schema type.

    Returns the schema key (e.g. "agent-state", "session-log") or None
    if the path doesn't match any known schema.
    """
    rel = _normalize_path(file_path)

    # Exact matches
    for pattern, schema_key in _EXACT_MATCHES:
        if rel.endswith(pattern):
            return schema_key

    # Directory + suffix matches
    for dir_prefix, suffix, schema_key in _SUFFIX_MATCHES:
        # Check if path contains the dir_prefix and ends with suffix
        idx = rel.find(dir_prefix)
        if idx != -1 and rel.endswith(suffix):
            return schema_key

    return None


def get_model_for_type(schema_type: str) -> Any | None:
    """
    Get the Pydantic model class for a schema type.

    Returns the model class, or None if the schema hasn't been
    implemented yet.
    """
    return _SCHEMA_REGISTRY.get(schema_type)


def validate_json_file(file_path: str, content: str) -> tuple[bool, str | None]:
    """
    Validate a JSON file's content against its Pydantic schema.

    Returns:
        (True, None) — valid, no schema match, or schema not yet implemented
        (False, error_message) — validation failed
    """
    rel = _normalize_path(file_path)

    # Only validate .claude/ JSON files
    if ".claude/" not in rel or not rel.endswith(".json"):
        return True, None

    # Parse JSON first — catch syntax errors regardless of schema
    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON in {rel}: {e}"

    # Match to schema type
    schema_type = match_schema_type(file_path)
    if schema_type is None:
        return True, None  # Unknown file type — allow through

    # Look up Pydantic model
    model = _SCHEMA_REGISTRY.get(schema_type)
    if model is None:
        return True, None  # Schema not yet implemented — allow through

    # Validate via Pydantic (TypeAdapter uses validate_json, BaseModel uses model_validate_json)
    try:
        if hasattr(model, "validate_json"):
            model.validate_json(content)
        else:
            model.model_validate_json(content)
    except Exception as e:
        label = SCHEMA_LABELS.get(schema_type, schema_type)
        # Extract the most useful part of Pydantic's error message
        error_msg = str(e)
        # Pydantic ValidationError has a nice .error_count() and .errors() but
        # we keep it simple — the string representation is already good
        return False, f"Validation failed for {label} file {rel}: {error_msg}"

    return True, None
