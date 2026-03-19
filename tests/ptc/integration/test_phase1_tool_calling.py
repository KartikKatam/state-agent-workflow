"""Phase 1 integration tests — direct Python execution in sandboxed containers.

PTC V2 has no internal tools. Agents write standard Python that runs directly
inside Docker containers with /workspace mounted and bridge networking enabled.
"""

from __future__ import annotations

import pytest

from conftest import assert_complete, await_execute


@pytest.mark.integration
@pytest.mark.phase1
async def test_read_file_via_open(container_manager):
    """open() reads files from /workspace mount."""
    code = "data = open('/workspace/_test_server.py').read()\nprint(len(data))"
    result = await await_execute(container_manager, "tool-1", "explorer", code)
    assert_complete(result)
    length = int(result["stdout"].strip())
    assert length > 0


@pytest.mark.integration
@pytest.mark.phase1
async def test_list_dir_via_os(container_manager):
    """os.listdir returns directory entries from /workspace."""
    code = "import os\nentries = os.listdir('/workspace')\nprint(len(entries))"
    result = await await_execute(container_manager, "tool-2", "explorer", code)
    assert_complete(result)
    count = int(result["stdout"].strip())
    assert count > 0


@pytest.mark.integration
@pytest.mark.phase1
async def test_file_search_via_os_walk(container_manager):
    """os.walk finds Python files across the project."""
    code = (
        "import os\n"
        "count = sum(1 for r, d, f in os.walk('/workspace') "
        "for name in f if name.endswith('.py'))\n"
        "print(count)"
    )
    result = await await_execute(container_manager, "tool-3", "explorer", code)
    assert_complete(result)
    count = int(result["stdout"].strip())
    assert count > 0


@pytest.mark.integration
@pytest.mark.phase1
async def test_write_file_to_workspace(container_manager):
    """Agents can write files to /workspace (rw mount)."""
    code = (
        "with open('/workspace/_test_write.txt', 'w') as f:\n"
        "    f.write('hello from PTC')\n"
        "print(open('/workspace/_test_write.txt').read())"
    )
    result = await await_execute(container_manager, "tool-4", "explorer", code)
    assert_complete(result)
    assert "hello from PTC" in result["stdout"]

    # Cleanup
    cleanup = "import os; os.remove('/workspace/_test_write.txt'); print('cleaned')"
    await await_execute(container_manager, "tool-4", "explorer", cleanup)


@pytest.mark.integration
@pytest.mark.phase1
async def test_subprocess_git_commands(container_manager):
    """subprocess.run can execute git commands."""
    code = (
        "import subprocess\n"
        "r = subprocess.run(['git', 'log', '--oneline', '-5'], "
        "capture_output=True, text=True, cwd='/workspace')\n"
        "print(r.stdout.strip())"
    )
    result = await await_execute(container_manager, "tool-5", "explorer", code)
    assert_complete(result)
    # Should get at least one line of git log
    assert len(result["stdout"].strip()) > 0


@pytest.mark.integration
@pytest.mark.phase1
async def test_package_importability(container_manager):
    """Pre-installed packages are importable."""
    code = (
        "import networkx as nx\n"
        "G = nx.DiGraph()\n"
        "G.add_edge('A', 'B')\n"
        "print(list(G.edges()))"
    )
    result = await await_execute(container_manager, "tool-6", "explorer", code)
    assert_complete(result)
    assert "('A', 'B')" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase1
async def test_error_handling_continues(container_manager):
    """Errors can be caught and execution continues."""
    code = (
        "try:\n"
        "    data = open('/nonexistent/file.py').read()\n"
        "except FileNotFoundError as e:\n"
        "    print(f'caught: {e}')\n"
        "print('continued')"
    )
    result = await await_execute(container_manager, "tool-7", "explorer", code)
    assert_complete(result)
    assert "continued" in result["stdout"]
