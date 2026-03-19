"""Phase 4 integration tests — sub-agent routing to parent containers."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.phase4]


async def test_sub_agent_routes_to_parent_container(container_manager):
    """Sub-agent is routed to parent's container after register_parent."""
    mgr = container_manager
    # Create parent REPL
    await mgr.get_or_create_repl("route-parent-1", "explorer")
    parent_container = mgr._agent_to_container["route-parent-1"]

    # Register sub-agent → parent, then create sub REPL
    await mgr.register_parent("route-sub-1", "route-parent-1")
    await mgr.get_or_create_repl("route-sub-1", "explorer")
    sub_container = mgr._agent_to_container["route-sub-1"]

    assert parent_container == sub_container


async def test_sub_agent_uses_different_socket(container_manager):
    """Sub-agent gets a different REPL index (socket) than parent."""
    mgr = container_manager
    parent_repl = await mgr.get_or_create_repl("socket-parent-1", "explorer")

    await mgr.register_parent("socket-sub-1", "socket-parent-1")
    sub_repl = await mgr.get_or_create_repl("socket-sub-1", "explorer")

    assert parent_repl.repl_index == 0
    assert sub_repl.repl_index == 1
    assert parent_repl.repl_index != sub_repl.repl_index


async def test_unregistered_sub_agent_gets_own_container(container_manager):
    """Agent without registered parent gets its own separate container."""
    mgr = container_manager
    await mgr.get_or_create_repl("unreg-parent-1", "explorer")
    parent_container = mgr._agent_to_container["unreg-parent-1"]

    # No register_parent call — just create directly
    await mgr.get_or_create_repl("unreg-other-1", "explorer")
    other_container = mgr._agent_to_container["unreg-other-1"]

    assert parent_container != other_container, (
        f"Expected different containers, both got {parent_container}"
    )


async def test_sub_agent_in_full_container_gets_own(container_manager):
    """Sub-agent overflows to new container when parent's is at max_repls (6)."""
    mgr = container_manager
    await mgr.get_or_create_repl("full-parent", "explorer")
    parent_container = mgr._agent_to_container["full-parent"]

    # Fill parent container to max (6 REPLs total: parent + 5 subs)
    for i in range(5):
        sub_id = f"full-fill-{i}"
        await mgr.register_parent(sub_id, "full-parent")
        await mgr.get_or_create_repl(sub_id, "explorer")

    # 7th REPL: register as sub but parent container is full
    await mgr.register_parent("full-overflow", "full-parent")
    try:
        await mgr.get_or_create_repl("full-overflow", "explorer")
        overflow_container = mgr._agent_to_container.get("full-overflow")
        # If it succeeded, it must have gone to a new container
        if overflow_container:
            assert overflow_container != parent_container, (
                "Overflow sub-agent should not be in the full parent container"
            )
    except RuntimeError as e:
        # Acceptable: raises error when container is full
        assert "max" in str(e).lower()
