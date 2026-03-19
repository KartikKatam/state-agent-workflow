"""Post-action validator registry and implementations.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from hooks.utils.schema_validator import validate_json_file

from scripts.daemon.state_manager import _machine_cache

_log = logging.getLogger("workflow_state")

ValidatorFn = Callable[[str, dict, str], list[str]]


def _validate_schema_generic(
    file_path: str, _tool_input: dict, tool_output: str
) -> list[str]:
    """Validate .claude/ JSON files against Pydantic schemas."""
    if not file_path or not file_path.endswith(".json") or ".claude/" not in file_path:
        return []
    content = tool_output
    if not content:
        try:
            content = Path(file_path).read_text()
        except Exception:
            return []
    valid, error = validate_json_file(file_path, content)
    if not valid and error:
        return [f"Schema validation: {error}"]
    return []


def _validate_ruff_critical(
    file_path: str, _tool_input: dict, _tool_output: str
) -> list[str]:
    """Run ruff on critical rules only (syntax errors, undefined names)."""
    if not file_path or not file_path.endswith(".py"):
        return []
    try:
        proc = subprocess.run(
            [
                "ruff",
                "check",
                "--select",
                "F821,F811,F401",
                file_path,
                "--output-format=json",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode != 0 and proc.stdout:
            issues = json.loads(proc.stdout)
            critical_codes = {"F821", "F811", "F401", "invalid-syntax"}
            return [
                f"{i['code']}: {i['message']} (line {i['location']['row']})"
                for i in issues
                if i.get("code") in critical_codes
            ]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return []


VALIDATOR_REGISTRY: dict[str, ValidatorFn] = {
    "validate_context_packet_schema": _validate_schema_generic,
    "validate_plan_schema": _validate_schema_generic,
    "validate_research_schema": _validate_schema_generic,
    "validate_session_log_schema": _validate_schema_generic,
    "validate_handoff_schema": _validate_schema_generic,
    "ruff_lint_critical": _validate_ruff_critical,
}


def _validate_post_actions() -> None:
    """Validate all post_actions validator names in loaded machines at startup."""
    for machine_id, machine in _machine_cache.items():
        for state in machine.states:
            for tool_key, validators in state.post_actions.items():
                for vname in validators:
                    if vname not in VALIDATOR_REGISTRY:
                        _log.warning(
                            "Machine %s, state %s: unknown validator %r in post_actions[%r]",
                            machine_id,
                            state.name,
                            vname,
                            tool_key,
                        )
