"""Tests for role-specific package availability."""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.phase1, pytest.mark.slow]


@pytest.mark.timeout(180)
async def test_explorer_packages(container_manager):
    """Explorer role has tree_sitter, jedi, radon, networkx, pandas, git, tiktoken."""
    mgr = container_manager
    agent = "explorer-pkg-1"

    start = time.monotonic()
    await mgr.get_or_create_repl(agent, "explorer")
    setup_time = time.monotonic() - start

    code = "import tree_sitter, jedi, radon, networkx, pandas, git, tiktoken; print('ok')"
    r = await mgr.execute_code(agent, code, 60)
    assert "ok" in r.get("stdout", ""), f"Explorer packages failed: {r}"
    print(f"Explorer setup time: {setup_time:.1f}s")


@pytest.mark.timeout(180)
async def test_coder_packages(container_manager):
    """Coder role has pyinstrument, coverage, bandit, unidiff, tiktoken."""
    mgr = container_manager
    agent = "coder-pkg-1"

    start = time.monotonic()
    await mgr.get_or_create_repl(agent, "coder")
    setup_time = time.monotonic() - start

    code = "import pyinstrument, coverage, bandit, unidiff, tiktoken; print('ok')"
    r = await mgr.execute_code(agent, code, 60)
    assert "ok" in r.get("stdout", ""), f"Coder packages failed: {r}"
    print(f"Coder setup time: {setup_time:.1f}s")


@pytest.mark.timeout(180)
async def test_tester_packages(container_manager):
    """Tester role has hypothesis, coverage, radon, tiktoken."""
    mgr = container_manager
    agent = "tester-pkg-1"

    start = time.monotonic()
    await mgr.get_or_create_repl(agent, "tester")
    setup_time = time.monotonic() - start

    code = "import hypothesis, coverage, radon, tiktoken; print('ok')"
    r = await mgr.execute_code(agent, code, 60)
    assert "ok" in r.get("stdout", ""), f"Tester packages failed: {r}"
    print(f"Tester setup time: {setup_time:.1f}s")


@pytest.mark.timeout(180)
async def test_auditor_packages(container_manager):
    """Auditor role has radon, cohesion, bandit, pydriller, tiktoken."""
    mgr = container_manager
    agent = "auditor-pkg-1"

    start = time.monotonic()
    await mgr.get_or_create_repl(agent, "auditor")
    setup_time = time.monotonic() - start

    code = "import radon, cohesion, bandit, pydriller, tiktoken; print('ok')"
    r = await mgr.execute_code(agent, code, 60)
    assert "ok" in r.get("stdout", ""), f"Auditor packages failed: {r}"
    print(f"Auditor setup time: {setup_time:.1f}s")


@pytest.mark.timeout(180)
async def test_researcher_packages(container_manager):
    """Researcher role has trafilatura, pypdf, pdfplumber, tiktoken."""
    mgr = container_manager
    agent = "researcher-pkg-1"

    start = time.monotonic()
    await mgr.get_or_create_repl(agent, "researcher")
    setup_time = time.monotonic() - start

    code = "import trafilatura, pypdf, pdfplumber, tiktoken; print('ok')"
    r = await mgr.execute_code(agent, code, 60)
    assert "ok" in r.get("stdout", ""), f"Researcher packages failed: {r}"
    print(f"Researcher setup time: {setup_time:.1f}s")


@pytest.mark.timeout(180)
async def test_strategist_packages(container_manager):
    """Strategist role has networkx, radon, tiktoken."""
    mgr = container_manager
    agent = "strategist-pkg-1"

    start = time.monotonic()
    await mgr.get_or_create_repl(agent, "strategist")
    setup_time = time.monotonic() - start

    code = "import networkx, radon, tiktoken; print('ok')"
    r = await mgr.execute_code(agent, code, 60)
    assert "ok" in r.get("stdout", ""), f"Strategist packages failed: {r}"
    print(f"Strategist setup time: {setup_time:.1f}s")
