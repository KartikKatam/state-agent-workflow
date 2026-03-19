#!/usr/bin/env python3
"""
Schema Validator Utility

Required-field presence checking for .claude/ JSON files.
No jsonschema dependency — pure Python dict traversal.

Maps file paths to schema types, then checks that critical fields
exist. Does NOT validate types, formats, or additional properties.
"""

import json
import os

# ---------------------------------------------------------------------------
# Required fields per schema type
#
# Each list contains dotted paths. "meta.type" means data["meta"]["type"]
# must exist. Only presence is checked — values can be anything.
# ---------------------------------------------------------------------------

REQUIRED_FIELDS: dict[str, list[str]] = {
    # --- Context Packets ---
    "codebase": [
        # Root: ["meta", "structure", "types", "configs", "patterns"]
        # meta: ["type", "created", "updated", "schema_version", "project_root"]
        "meta",
        "meta.type",
        "meta.created",
        "meta.updated",
        "meta.schema_version",
        "meta.project_root",
        "structure",
        "types",
        "configs",
        "patterns",
    ],
    "feature-context": [
        # Root: ["meta", "feature", "touchpoints", "dependencies"]
        # meta: ["type", "created", "updated", "schema_version", "feature_name"]
        "meta",
        "meta.type",
        "meta.created",
        "meta.updated",
        "meta.schema_version",
        "meta.feature_name",
        "feature",
        "touchpoints",
        "dependencies",
    ],
    "query-result": [
        # Root: ["meta", "query", "answer", "sources"]
        # meta: ["type", "created", "schema_version", "requester"]
        # query: ["question", "scope"]
        # answer: ["summary", "confidence"]
        "meta",
        "meta.type",
        "meta.created",
        "meta.schema_version",
        "meta.requester",
        "query",
        "query.question",
        "query.scope",
        "answer",
        "answer.summary",
        "answer.confidence",
        "sources",
    ],
    "design-review": [
        # Root: ["feature", "design_doc_path", "design_doc_hash",
        #        "status", "reviewed_at", "reviewed_by"]
        "feature",
        "design_doc_path",
        "design_doc_hash",
        "status",
        "reviewed_at",
        "reviewed_by",
    ],
    # --- Plans ---
    "implementation-plan": [
        # Root: ["meta", "feature", "chunks", "quality_gates", "module_tests"]
        # meta: ["type", "created", "schema_version", "feature_name", "status"]
        "meta",
        "meta.type",
        "meta.created",
        "meta.schema_version",
        "meta.feature_name",
        "meta.status",
        "feature",
        "chunks",
        "quality_gates",
        "module_tests",
    ],
    # --- Session Logs ---
    "session-log": [
        # Root: ["meta", "status", "phase"]
        # meta: ["feature", "chunk", "started_at", "session_id"]
        "meta",
        "meta.feature",
        "meta.chunk",
        "meta.started_at",
        "meta.session_id",
        "status",
        "phase",
    ],
    # --- Planning Logs ---
    "planning-log": [
        # Root: ["meta", "status", "phase", "source_design_doc"]
        # meta: ["feature", "started_at", "session_id"]
        "meta",
        "meta.feature",
        "meta.started_at",
        "meta.session_id",
        "source_design_doc",
        "status",
        "phase",
    ],
    # --- Research ---
    "research-persistent": [
        # Root: ["id", "library", "type", "documentation", "sources", "meta"]
        # meta: ["created_at", "last_updated"]
        "id",
        "library",
        "type",
        "documentation",
        "sources",
        "meta",
        "meta.created_at",
        "meta.last_updated",
    ],
    "research-ephemeral": [
        # Root: ["id", "query", "answer", "meta"]
        # meta: ["agent_id", "created_at", "expires_at"]
        "id",
        "query",
        "answer",
        "meta",
        "meta.agent_id",
        "meta.created_at",
        "meta.expires_at",
    ],
    "research-index": [
        # Root: ["persistent", "ephemeral", "meta"]
        # meta: ["last_cleanup", "total_persistent", "total_ephemeral"]
        "persistent",
        "ephemeral",
        "meta",
        "meta.last_cleanup",
        "meta.total_persistent",
        "meta.total_ephemeral",
    ],
    # --- Memory ---
    "memory": [
        # No strict required fields — memory files evolve frequently
        # and the primary store is memory.db, not JSON files
    ],
}


# ---------------------------------------------------------------------------
# Human-readable labels for error messages
# ---------------------------------------------------------------------------

SCHEMA_LABELS: dict[str, str] = {
    "codebase": "codebase context",
    "feature-context": "feature context",
    "query-result": "query result",
    "design-review": "design review",
    "implementation-plan": "implementation plan",
    "session-log": "session log",
    "planning-log": "planning session log",
    "research-persistent": "persistent research",
    "research-ephemeral": "ephemeral research",
    "research-index": "research index",
    "memory": "memory",
}


# ---------------------------------------------------------------------------
# Path → schema type matching
#
# Checked in order. Exact matches first, then directory+suffix patterns.
# ---------------------------------------------------------------------------

# (relative_path, schema_key)
_EXACT_MATCHES: list[tuple[str, str]] = [
    (".claude/context/_codebase.json", "codebase"),
    (".claude/research/_index.json", "research-index"),
]

# (dir_prefix, suffix, schema_key)
_SUFFIX_MATCHES: list[tuple[str, str, str]] = [
    (".claude/context/queries/", ".json", "query-result"),
    (".claude/context/", "-design-review.json", "design-review"),
    (".claude/context/", "-context.json", "feature-context"),
    (".claude/plans/", "-plan.json", "implementation-plan"),
    (".claude/logs/", "-planning-log.json", "planning-log"),
    (".claude/logs/", "-log.json", "session-log"),
    (".claude/research/persistent/", ".json", "research-persistent"),
    (".claude/research/ephemeral/", ".json", "research-ephemeral"),
    (".claude/memory/", ".json", "memory"),
]


def _normalize_path(file_path: str) -> str:
    """Normalize to a relative path starting with .claude/."""
    if os.path.isabs(file_path):
        try:
            rel = os.path.relpath(file_path)
        except ValueError:
            rel = file_path
    else:
        rel = file_path

    # Ensure consistent forward slashes
    return rel.replace("\\", "/")


def match_schema_type(file_path: str) -> str | None:
    """
    Match a file path to its schema type.

    Returns the schema key (e.g. "codebase", "session-log") or None
    if the path doesn't match any known schema.
    """
    rel = _normalize_path(file_path)

    # Exact matches
    for pattern, schema_key in _EXACT_MATCHES:
        if rel == pattern:
            return schema_key

    # Directory + suffix matches
    for dir_prefix, suffix, schema_key in _SUFFIX_MATCHES:
        if rel.startswith(dir_prefix) and rel.endswith(suffix):
            return schema_key

    return None


def check_required_fields(data: dict, fields: list[str]) -> list[str]:
    """
    Check that all required fields are present in data.

    Supports dotted paths: "meta.type" checks data["meta"]["type"].

    Returns list of missing field paths (empty = all present).
    """
    missing = []
    for field in fields:
        obj = data
        for part in field.split("."):
            if not isinstance(obj, dict) or part not in obj:
                missing.append(field)
                break
            obj = obj[part]
    return missing


def validate_json_file(file_path: str, content: str) -> tuple[bool, str | None]:
    """
    Validate a JSON file's content against its schema's required fields.

    Returns:
        (True, None) — valid or no schema match (allow through)
        (False, error_message) — validation failed
    """
    # Only validate .claude/ JSON files
    rel = _normalize_path(file_path)
    if not rel.startswith(".claude/") or not rel.endswith(".json"):
        return True, None

    # Parse JSON
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON in {rel}: {e}"

    # Must be a dict at root
    if not isinstance(data, dict):
        return (
            False,
            f"Expected JSON object at root in {rel}, got {type(data).__name__}",
        )

    # Match to schema
    schema_type = match_schema_type(file_path)
    if schema_type is None:
        return True, None  # Unknown file type — allow through

    # Check required fields
    fields = REQUIRED_FIELDS.get(schema_type, [])
    if not fields:
        return True, None  # No required fields for this type

    missing = check_required_fields(data, fields)
    if missing:
        label = SCHEMA_LABELS.get(schema_type, schema_type)
        if len(missing) == 1:
            return (
                False,
                f"Missing required field '{missing[0]}' in {label} file: {rel}",
            )
        else:
            fields_str = "', '".join(missing)
            return (
                False,
                f"Missing required fields '{fields_str}' in {label} file: {rel}",
            )

    return True, None
