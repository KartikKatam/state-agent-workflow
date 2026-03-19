#!/usr/bin/env python3
"""
PreToolUse Validation Hook: Type-dispatched validation for Task delegation prompts.

Every Task prompt must be a JSON object with a "type" field that selects the
schema: "targeted", "guided", "tdd_chunk", "exploration", or "research".
Each type has different required fields enforced by this hook.

Hook type: PreToolUse (matcher: Task)
Exit code 2 = BLOCK (deny the tool call)
Exit code 0 = ALLOW (permit the tool call)
"""

import json
import os
import re
import sys

from pydantic import ValidationError

from schemas.delegation_prompt import (
    VALID_TYPES,
    DelegationPromptAdapter,
)


def get_hook_input() -> dict:
    """Read hook input from environment or stdin."""
    hook_data = os.environ.get("CLAUDE_HOOK_DATA")
    if hook_data:
        return json.loads(hook_data)

    if not sys.stdin.isatty():
        return json.load(sys.stdin)

    return {}


def _deny(reason: str):
    """Print deny output and exit with code 2."""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output))
    sys.exit(2)


# ---------------------------------------------------------------------------
# Checks that can't be expressed in Pydantic
# ---------------------------------------------------------------------------

def _check_vague_discovery_verbs(text: str) -> str | None:
    """Check for standalone vague discovery verbs without specific targets."""
    vague_patterns = [
        r"\bexplore\b(?!\s+(?:whether|if|/|`|line|function|class|method))",
        r"\binvestigate\b(?!\s+(?:whether|if|/|`|line|function|class|method))",
        r"\bfigure\s+out\b(?!\s+(?:whether|if|/|`|line|function|class|method))",
        r"\blook\s+into\b(?!\s+(?:whether|if|/|`|line|function|class|method))",
    ]

    for pattern in vague_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            verb = match.group(0).strip()
            start = max(0, match.start() - 20)
            end = min(len(text), match.end() + 40)
            context = text[start:end].replace("\n", " ").strip()
            return (
                "Vague discovery verb '{}' in task: '...{}...'. "
                "Replace with coordinates or use type 'guided' with specific unknowns."
            ).format(verb, context)

    return None


# ---------------------------------------------------------------------------
# Pydantic error formatting
# ---------------------------------------------------------------------------

# Map Pydantic error types/locations to the helpful messages agents expect.
# The keys are (error_type, field_path_prefix) tuples — we match the most
# specific entry first.

_FIELD_ERROR_MESSAGES: dict[str, str] = {
    # Common required fields
    "task": (
        "Missing required 'task' field. Describe what the delegate should accomplish."
    ),
    "known_context": (
        "Missing required 'known_context' object. Run reasoning chain Q1-Q2 to "
        "surface file coordinates and findings."
    ),
    "output_contract": (
        "Missing required 'output_contract' object with at least a 'path' field."
    ),
    "output_contract.path": (
        "output_contract.path is empty. Specify where the delegate writes output."
    ),
    # guided
    "unknowns": (
        "Type 'guided' requires non-empty 'unknowns' array. "
        "List what the delegate needs to discover or decide. "
        "If you have no unknowns, use type 'targeted' instead."
    ),
    "scope_boundary": (
        "Type 'guided' requires 'scope_boundary' object. "
        "The delegate has discretion -- bound it with do_not, tool_budget, "
        "or file_set to prevent scope creep."
    ),
    # tdd_chunk
    "test_specifications": (
        "Type 'tdd_chunk' requires 'test_specifications' object. "
        "Include at least pass_a with test names and descriptions."
    ),
    "test_specifications.pass_a": (
        "Type 'tdd_chunk' requires test_specifications.pass_a with at least "
        "one test. TDD starts with unit tests -- specify them."
    ),
    "success_criteria": (
        "Type 'tdd_chunk' requires non-empty 'success_criteria'. "
        "Include pass gate conditions (e.g., 'All Pass A tests pass')."
    ),
    # exploration
    "exploration_scope": (
        "Type 'exploration' requires 'exploration_scope' object. "
        "Include primary_targets (what this agent explores), peer_scopes "
        "(what other agents cover), and extension_policy."
    ),
    "exploration_scope.primary_targets": (
        "Type 'exploration' requires exploration_scope.primary_targets with "
        "at least one entry. Specify bounded exploration targets, not vague topics."
    ),
    "essential_output": None,  # varies by type, handled dynamically
    # research
    "research_scope": (
        "Type 'research' requires 'research_scope' object. "
        "Include primary_targets (specific research topics), peer_scopes, "
        "and extension_policy."
    ),
    "research_scope.primary_targets": (
        "Type 'research' requires research_scope.primary_targets with "
        "at least one entry. Specify concrete research targets."
    ),
    "source_directives": (
        "Type 'research' requires 'source_directives' object. "
        "Include preferred_sources, blocked_sources, and required_depth."
    ),
}

# essential_output messages per type
_ESSENTIAL_OUTPUT_BY_TYPE: dict[str, str] = {
    "exploration": (
        "Type 'exploration' requires non-empty 'essential_output'. "
        "List what the agent MUST deliver with high confidence."
    ),
    "research": (
        "Type 'research' requires non-empty 'essential_output'. "
        "List what MUST be answered with confidence."
    ),
}


def _loc_to_path(loc: tuple) -> str:
    """Convert Pydantic error location tuple to dot-separated path.

    Discriminated unions prepend the type tag to the location (e.g.,
    ('guided', 'unknowns') instead of ('unknowns',)). Strip the leading
    type tag so our field mapping keys match.
    """
    parts = [str(p) for p in loc if p != "__root__"]
    # Strip leading discriminator tag if present
    if parts and parts[0] in VALID_TYPES:
        parts = parts[1:]
    return ".".join(parts)


def _format_pydantic_error(e: ValidationError, data: dict) -> str:
    """Convert a Pydantic ValidationError into a helpful agent-facing message.

    Returns the first actionable error message, matching the original hook's
    behavior of returning a single error string.
    """
    prompt_type = data.get("type", "")

    for error in e.errors():
        loc_path = _loc_to_path(error["loc"])
        error_type = error["type"]
        error_msg = error["msg"]

        # Value errors from our model/field validators — these already have
        # the carefully-crafted messages we wrote.
        if error_type == "value_error":
            return error_msg

        # Missing field errors — map to helpful messages
        if error_type == "missing":
            if loc_path in _FIELD_ERROR_MESSAGES:
                msg = _FIELD_ERROR_MESSAGES[loc_path]
                if msg is not None:
                    return msg
                # essential_output varies by type
                if loc_path == "essential_output" and prompt_type in _ESSENTIAL_OUTPUT_BY_TYPE:
                    return _ESSENTIAL_OUTPUT_BY_TYPE[prompt_type]

        # too_short — list/string minimum length violations
        if error_type == "too_short":
            if loc_path in _FIELD_ERROR_MESSAGES:
                msg = _FIELD_ERROR_MESSAGES[loc_path]
                if msg is not None:
                    return msg
                if loc_path == "essential_output" and prompt_type in _ESSENTIAL_OUTPUT_BY_TYPE:
                    return _ESSENTIAL_OUTPUT_BY_TYPE[prompt_type]

        # string_too_short on task
        if error_type == "string_too_short" and loc_path == "task":
            return _FIELD_ERROR_MESSAGES["task"]

        # Wrong type for a field (e.g., scope_boundary got a list instead of dict)
        if loc_path in _FIELD_ERROR_MESSAGES and _FIELD_ERROR_MESSAGES[loc_path]:
            return _FIELD_ERROR_MESSAGES[loc_path]

    # Fallback: format the first error in a readable way
    first = e.errors()[0]
    loc_path = _loc_to_path(first["loc"])
    if loc_path:
        return "Validation error at '{}': {}".format(loc_path, first["msg"])
    return "Validation error: {}".format(first["msg"])


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------


def validate_prompt(prompt: str) -> str | None:
    """Parse and validate a JSON delegation prompt. Returns first error or None."""

    # Must be valid JSON
    try:
        data = json.loads(prompt)
    except (json.JSONDecodeError, ValueError):
        return (
            "Delegation prompt must be a JSON object. "
            "Compose a JSON prompt with 'type', 'task', 'known_context', "
            "and 'output_contract' fields. See schemas/ for type-specific schemas."
        )

    if not isinstance(data, dict):
        return "Delegation prompt must be a JSON object, got {}.".format(type(data).__name__)

    # type field required — check before Pydantic so we get the same message
    prompt_type = data.get("type")
    if not prompt_type or prompt_type not in VALID_TYPES:
        return (
            "Missing or invalid 'type' field. Must be one of: {}. "
            "Choose 'targeted' (you have file coordinates), 'guided' (delegate "
            "needs judgment), 'tdd_chunk' (TDD implementation with test specs), "
            "'exploration' (scoped codebase exploration), or 'research' (source-targeted research)."
        ).format(", ".join(VALID_TYPES))

    # Pydantic validation (type-dispatched via discriminated union)
    try:
        DelegationPromptAdapter.validate_python(data)
    except ValidationError as e:
        return _format_pydantic_error(e, data)

    # Vague verbs in task field (regex — can't be a Pydantic validator)
    error = _check_vague_discovery_verbs(data.get("task", ""))
    if error:
        return error

    return None


def main():
    """Main hook handler."""
    hook_input = get_hook_input()

    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    if tool_name != "Task":
        return

    prompt = tool_input.get("prompt", "")
    if not prompt:
        return

    if "SKIP_DELEGATION_VALIDATION" in prompt:
        return

    error = validate_prompt(prompt)
    if error:
        _deny(error)


if __name__ == "__main__":
    main()
