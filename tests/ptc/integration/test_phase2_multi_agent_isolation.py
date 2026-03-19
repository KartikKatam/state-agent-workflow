"""Phase 2 integration tests — multi-agent isolation in sandboxed containers."""

from __future__ import annotations

import pytest

from conftest import assert_complete, await_execute


@pytest.mark.integration
@pytest.mark.phase2
async def test_two_agents_different_roles_different_containers(container_manager):
    """Two agents with different roles get different containers."""
    r1 = await await_execute(container_manager, "iso-explorer-1", "explorer", "print('ok')")
    r2 = await await_execute(container_manager, "iso-coder-1", "coder", "print('ok')")
    assert_complete(r1)
    assert_complete(r2)

    mapping = container_manager._agent_to_container
    c1 = mapping["iso-explorer-1"]
    c2 = mapping["iso-coder-1"]
    assert c1 != c2, f"Expected different containers, both got {c1}"


@pytest.mark.integration
@pytest.mark.phase2
async def test_two_agents_same_role_separate_containers(container_manager):
    """Two agents with the same role still get separate containers."""
    r1 = await await_execute(container_manager, "iso-exp-a", "explorer", "print('ok')")
    r2 = await await_execute(container_manager, "iso-exp-b", "explorer", "print('ok')")
    assert_complete(r1)
    assert_complete(r2)

    mapping = container_manager._agent_to_container
    ca = mapping["iso-exp-a"]
    cb = mapping["iso-exp-b"]
    assert ca != cb, f"Expected different containers for same-role agents, both got {ca}"


@pytest.mark.integration
@pytest.mark.phase2
async def test_agent_namespace_isolation(container_manager):
    """Variables set by one agent are not visible to another."""
    # Agent A sets x=100
    r1 = await await_execute(container_manager, "iso-ns-a", "explorer", "x = 100")
    assert_complete(r1)

    # Agent B sets x=200
    r2 = await await_execute(container_manager, "iso-ns-b", "explorer", "x = 200")
    assert_complete(r2)

    # Agent A reads x — should still be 100
    r3 = await await_execute(container_manager, "iso-ns-a", "explorer", "print(x)")
    assert_complete(r3)
    assert "100" in r3["stdout"], f"Expected 100, got: {r3['stdout']}"


@pytest.mark.integration
@pytest.mark.phase2
async def test_agent_crash_does_not_affect_other(container_manager):
    """An exception in one agent does not affect another agent."""
    # Agent A runs successfully first
    r1 = await await_execute(container_manager, "iso-crash-a", "explorer", "print('before')")
    assert_complete(r1)

    # Agent B crashes with ZeroDivisionError
    r2 = await await_execute(container_manager, "iso-crash-b", "explorer", "1/0")
    assert r2.get("type") == "error" or "ZeroDivisionError" in r2.get("stderr", "") + r2.get("stdout", "")

    # Agent A still works fine
    r3 = await await_execute(container_manager, "iso-crash-a", "explorer", "print(1)")
    assert_complete(r3)
    assert "1" in r3["stdout"]


@pytest.mark.integration
@pytest.mark.phase2
async def test_agent_identifiers_tracked_correctly(container_manager):
    """Creating multiple agents tracks all of them in _agent_to_container."""
    agents = [
        ("iso-track-1", "explorer"),
        ("iso-track-2", "coder"),
        ("iso-track-3", "tester"),
        ("iso-track-4", "auditor"),
    ]
    for agent_id, role in agents:
        r = await await_execute(container_manager, agent_id, role, "print('ok')")
        assert_complete(r)

    mapping = container_manager._agent_to_container
    for agent_id, _ in agents:
        assert agent_id in mapping, f"Agent {agent_id} not in _agent_to_container"
    # Verify all have distinct containers
    containers = [mapping[aid] for aid, _ in agents]
    assert len(set(containers)) == 4, f"Expected 4 unique containers, got: {containers}"
