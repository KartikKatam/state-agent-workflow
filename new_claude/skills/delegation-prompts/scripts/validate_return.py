#!/usr/bin/env python3
"""
Return validation utility for sub-agent delegation returns.

Validates that a sub-agent's return string is a JSON object matching the
return schema for its declared delegation_type. Called by the parent agent
(via PTC or directly) after receiving a sub-agent return.

NOT a hook — a utility script importable by the SubagentStop hook and
callable by parent agents.

Uses Pydantic models from schemas.delegation_return for validation.

Usage:
    # As a module
    from validate_return import validate_return
    result = validate_return(raw_return_string)
    # result: {"valid": True/False, "errors": [...], "extracted": {...}}

    # As a script (reads from stdin)
    echo '{"delegation_type": "targeted", ...}' | python validate_return.py
"""

import json
import os
import sys

# Ensure project root is importable so `schemas` package resolves
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from pydantic import TypeAdapter, ValidationError

from schemas.delegation_return import DelegationReturn

_adapter = TypeAdapter(DelegationReturn)

VALID_RETURN_TYPES = ("targeted", "guided", "tdd_chunk", "exploration", "research")


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from mixed text.

    First tries json.loads on the full string. If that fails, tries to find
    the outermost {...} block via brace matching.
    """
    # Try direct parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Try to find JSON object in mixed text via brace matching
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    data = json.loads(candidate)
                    if isinstance(data, dict):
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass
                break

    return None


# Human-friendly names for each delegation type tag
_TYPE_LABELS = {
    "targeted": "Targeted",
    "guided": "Guided",
    "exploration": "Exploration",
    "research": "Research",
    "tdd_chunk": "TDD chunk",
}


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    """Convert Pydantic ValidationError into human-readable error messages.

    Produces messages similar to the original manual validators so that
    downstream consumers (hooks, parent agents) see familiar text.
    """
    errors: list[str] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        msg = err.get("msg", "")
        err_type = err.get("type", "")

        # The first element of loc is the discriminator tag (e.g. "targeted").
        # Extract it for a human-friendly prefix, then use the rest as path.
        tag = ""
        field_parts: list[str] = []
        for part in loc:
            s = str(part)
            if not tag and s in _TYPE_LABELS:
                tag = s
            else:
                field_parts.append(s)

        label = _TYPE_LABELS.get(tag, tag.replace("_", " ").title()) if tag else ""
        field_path = ".".join(field_parts)

        # Map Pydantic error types to user-friendly messages
        if err_type == "missing":
            if field_path:
                errors.append(
                    f"{label} return requires '{field_path}'.".strip()
                )
            else:
                errors.append(f"Missing required field. {msg}")
        elif err_type == "literal_error":
            if "delegation_type" in field_path:
                errors.append(
                    f"Invalid delegation_type. Must be one of: "
                    f"{', '.join(VALID_RETURN_TYPES)}."
                )
            elif "status" in field_path:
                errors.append(
                    "Invalid status. Must be one of: completed, partial, failed."
                )
            else:
                errors.append(f"Invalid value for '{field_path}': {msg}")
        elif err_type in ("dict_type", "model_type"):
            errors.append(
                f"{label} return requires '{field_path}' to be an object.".strip()
            )
        elif err_type == "list_type":
            errors.append(
                f"{label} return requires '{field_path}' to be an array.".strip()
            )
        elif err_type == "string_type":
            errors.append(
                f"{label} return requires '{field_path}' to be a string.".strip()
            )
        elif "union_tag_invalid" in err_type:
            errors.append(
                f"Invalid delegation_type. Must be one of: "
                f"{', '.join(VALID_RETURN_TYPES)}."
            )
        else:
            if field_path:
                errors.append(f"Validation error at '{field_path}': {msg}")
            else:
                errors.append(f"Validation error: {msg}")

    return errors


def validate_return(raw: str) -> dict:
    """Validate a sub-agent return string.

    Returns:
        {
            "valid": bool,
            "errors": list[str],
            "extracted": dict | None  # The parsed JSON if extraction succeeded
        }
    """
    # Try to extract JSON
    data = _extract_json(raw)
    if data is None:
        return {
            "valid": False,
            "errors": [
                "Return is not valid JSON. Your return must be a single JSON object "
                "with at minimum: delegation_type, status. See the return_schema in "
                "your delegation prompt."
            ],
            "extracted": None,
        }

    # Pre-check: is delegation_type present at all? Give a clear message
    # before Pydantic's discriminator error, which can be confusing.
    if "delegation_type" not in data:
        return {
            "valid": False,
            "errors": [
                "Return JSON must include 'delegation_type' field matching your "
                "delegation type."
            ],
            "extracted": data,
        }

    try:
        _adapter.validate_python(data)
        return {"valid": True, "errors": [], "extracted": data}
    except ValidationError as e:
        errors = _format_pydantic_errors(e)
        return {"valid": False, "errors": errors, "extracted": data}


def main():
    """Read return string from stdin, validate, print result."""
    raw = sys.stdin.read()
    result = validate_return(raw)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
