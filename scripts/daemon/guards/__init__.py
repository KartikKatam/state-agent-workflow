"""Guard registry and parameterized evaluation engine.

Extracted from scripts/workflow_state.py (Chunk 2 of daemon hardening plan).
Strict mode: unregistered guards FAIL (not pass). Supports name:param syntax.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from scripts.daemon.state_manager import _get_agent_state_dict, _get_system_state_dict

_log = logging.getLogger("workflow_state")

# Guard function signature: (agent_id, agent_state_dict, system_state_dict, param) -> bool
# param is optional (None for non-parameterized guards)
GuardFn = Callable[[str, dict | None, dict | None, str | None], bool]

_GUARD_REGISTRY: dict[str, GuardFn] = {}


def evaluate_guards(
    guards: list[str], agent_id: str
) -> tuple[bool, dict[str, bool], str]:
    """Evaluate all guards for a transition.

    Returns (all_passed, results_dict, failure_reason).
    Strict mode: unregistered guards FAIL with a logged warning.
    Supports parameterized guards via name:param syntax.
    """
    if not guards:
        return True, {}, ""

    agent_state = _get_agent_state_dict(agent_id)
    system_state = _get_system_state_dict()

    results: dict[str, bool] = {}
    for guard_expr in guards:
        # Split on first ':' for parameterized guards (e.g. think_chosen:APPROVE)
        name, _, param = guard_expr.partition(":")
        param_val: str | None = param if param else None

        fn = _GUARD_REGISTRY.get(name)
        if fn is None:
            _log.warning(
                "Guard %r not registered — FAILING (strict mode)", guard_expr
            )
            results[guard_expr] = False
            continue
        try:
            results[guard_expr] = fn(agent_id, agent_state, system_state, param_val)
        except TypeError:
            # Backward compat: try 3-arg signature for old-style guards
            try:
                results[guard_expr] = fn(agent_id, agent_state, system_state)  # type: ignore[call-arg]
            except Exception:
                _log.warning(
                    "Guard %r raised exception — FAILING (strict mode)",
                    guard_expr,
                    exc_info=True,
                )
                results[guard_expr] = False
        except Exception:
            _log.warning(
                "Guard %r raised exception — FAILING (strict mode)",
                guard_expr,
                exc_info=True,
            )
            results[guard_expr] = False

    failed = [name for name, passed in results.items() if not passed]
    if failed:
        return False, results, f"Guards failed: {', '.join(failed)}"
    return True, results, ""
