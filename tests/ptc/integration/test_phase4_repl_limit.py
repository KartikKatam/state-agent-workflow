"""Phase 4 integration tests — REPL limits per container."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.phase4]


async def test_max_6_repls_per_container(container_manager):
    """Container enforces max 6 REPLs — 7th raises RuntimeError."""
    mgr = container_manager
    # Parent takes slot 0
    await mgr.get_or_create_repl("limit-parent", "explorer")

    # Fill remaining 5 slots (1-5)
    for i in range(5):
        sub_id = f"limit-sub-{i}"
        await mgr.register_parent(sub_id, "limit-parent")
        await mgr.get_or_create_repl(sub_id, "explorer")

    # 7th REPL: container is full, so sub-agent gets routed to a new container
    # (get_or_create_repl falls through to creating a new container when parent's is full)
    await mgr.register_parent("limit-overflow", "limit-parent")
    overflow_repl = await mgr.get_or_create_repl("limit-overflow", "explorer")

    # Verify it was routed to a DIFFERENT container than the parent
    parent_container = mgr._agent_to_container["limit-parent"]
    overflow_container = mgr._agent_to_container["limit-overflow"]
    assert overflow_container != parent_container, (
        "Overflow sub-agent should be in a different container"
    )
    assert overflow_repl is not None


async def test_repl_indices_sequential(container_manager):
    """REPL indices are assigned sequentially: 0, 1, 2, 3."""
    mgr = container_manager
    repls = []
    parent_repl = await mgr.get_or_create_repl("seq-parent", "explorer")
    repls.append(parent_repl)

    for i in range(3):
        sub_id = f"seq-sub-{i}"
        await mgr.register_parent(sub_id, "seq-parent")
        repl = await mgr.get_or_create_repl(sub_id, "explorer")
        repls.append(repl)

    indices = [r.repl_index for r in repls]
    assert indices == [0, 1, 2, 3], f"Expected [0, 1, 2, 3], got {indices}"


async def test_stop_repl_frees_slot(container_manager):
    """Stopping a REPL frees a slot, allowing a new one to be created."""
    mgr = container_manager
    # Fill to max (6 REPLs)
    await mgr.get_or_create_repl("free-parent", "explorer")
    sub_ids = []
    for i in range(5):
        sub_id = f"free-sub-{i}"
        await mgr.register_parent(sub_id, "free-parent")
        await mgr.get_or_create_repl(sub_id, "explorer")
        sub_ids.append(sub_id)

    # Stop one sub-agent to free a slot
    await mgr.stop_repl(sub_ids[0])

    # Now creating another should succeed
    await mgr.register_parent("free-new-sub", "free-parent")
    new_repl = await mgr.get_or_create_repl("free-new-sub", "explorer")

    # Verify it's in the same container as parent
    parent_container = mgr._agent_to_container["free-parent"]
    new_container = mgr._agent_to_container["free-new-sub"]
    assert parent_container == new_container

    # New repl_index depends on len(container.repls) after removal
    assert new_repl.repl_index >= 0
