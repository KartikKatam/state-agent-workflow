"""Decision guard implementations — think_chosen, think_completed.

New in Chunk 2 of daemon hardening plan. These guards route transitions
based on the agent's Think tool CHOSEN declaration.
"""

from __future__ import annotations

from scripts.daemon.guards import _GUARD_REGISTRY


def _guard_think_chosen(
    _agent_id: str, agent: dict | None, _system: dict | None, param: str | None = None
) -> bool:
    """Check if agent's last CHOSEN value matches param AND was for the current state.

    Usage in SM: "guards": ["think_chosen:APPROVE"]
    Passes when agent_state.last_think_chosen == "APPROVE"
    AND agent_state.last_think_state == agent_state.current_state.
    """
    if not agent or not param:
        return False
    return (
        agent.get("last_think_chosen") == param
        and agent.get("last_think_state") == agent.get("current_state")
    )


def _guard_think_completed(
    _agent_id: str, agent: dict | None, _system: dict | None, _param: str | None = None
) -> bool:
    """Check if agent completed a think for the current state (any CHOSEN value).

    Usage in SM: "guards": ["think_completed"]
    Passes when agent_state.last_think_state == agent_state.current_state.
    """
    if not agent:
        return False
    return agent.get("last_think_state") == agent.get("current_state")


# ---------------------------------------------------------------------------
# Guard registration
# ---------------------------------------------------------------------------
_GUARD_REGISTRY.update(
    {
        "think_chosen": _guard_think_chosen,
        "think_completed": _guard_think_completed,
    }
)
