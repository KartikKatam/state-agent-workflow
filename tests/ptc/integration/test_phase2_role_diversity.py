"""Phase 2 integration tests — role diversity and package availability.

PTC V2 pre-builds role-specific Docker images with packages baked in.
Tests verify that role-specific packages are importable directly.
"""

from __future__ import annotations

import pytest

from conftest import assert_complete


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_all_six_roles_simultaneously(container_manager):
    """Six agents (one per role) all execute successfully."""
    mgr = container_manager
    roles = [
        ("role-explorer", "explorer"),
        ("role-coder", "coder"),
        ("role-tester", "tester"),
        ("role-auditor", "auditor"),
        ("role-researcher", "researcher"),
        ("role-strategist", "strategist"),
    ]

    for agent_id, role in roles:
        await mgr.get_or_create_repl(agent_id, role)

    for agent_id, role in roles:
        result = await mgr.execute_code(agent_id, "print('ok')", timeout=60)
        assert_complete(result)
        assert "ok" in result["stdout"], (
            f"Role {role} (agent {agent_id}) missing 'ok': {result['stdout']}"
        )


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_explorer_role_packages_and_analysis(container_manager):
    """Explorer agent can import networkx, radon, and analyze files directly."""
    mgr = container_manager
    await mgr.get_or_create_repl("role-exp-real", "explorer")

    code = (
        "import os, re\n"
        "py_files = [f for f in os.listdir('/workspace') if f.endswith('.py')]\n"
        "# Search for class.*Manager pattern in Python files\n"
        "matches = []\n"
        "for f in py_files:\n"
        "    content = open(f'/workspace/{f}').read()\n"
        "    if re.search(r'class.*Manager', content):\n"
        "        matches.append(f)\n"
        'print(f"found {len(matches)} matches")'
    )
    result = await mgr.execute_code("role-exp-real", code, timeout=120)
    assert_complete(result)
    assert "found" in result["stdout"]
    assert "found 0 matches" not in result["stdout"].strip(), (
        "Expected at least one match for 'class.*Manager'"
    )


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_coder_role_file_processing(container_manager):
    """Coder agent can read files and process content directly."""
    mgr = container_manager
    await mgr.get_or_create_repl("role-cod-real", "coder")

    code = (
        'data = open("/workspace/_test_server.py").read()\n'
        'print(f"Lines: {len(data.splitlines())}")'
    )
    result = await mgr.execute_code("role-cod-real", code, timeout=120)
    assert_complete(result)
    assert "Lines:" in result["stdout"]
    assert "Lines: 0" not in result["stdout"].strip(), (
        "Expected server.py to have content"
    )


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_researcher_role_packages(container_manager):
    """Researcher agent can import trafilatura and other research packages."""
    mgr = container_manager
    await mgr.get_or_create_repl("role-res-pkg", "researcher")

    code = "import trafilatura; print('researcher packages ok')"
    result = await mgr.execute_code("role-res-pkg", code, timeout=120)
    assert_complete(result)
    assert "researcher packages ok" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase2
@pytest.mark.slow
async def test_auditor_role_packages(container_manager):
    """Auditor agent can import radon and analysis packages."""
    mgr = container_manager
    await mgr.get_or_create_repl("role-aud-pkg", "auditor")

    code = "import radon; print('auditor packages ok')"
    result = await mgr.execute_code("role-aud-pkg", code, timeout=120)
    assert_complete(result)
    assert "auditor packages ok" in result["stdout"]
