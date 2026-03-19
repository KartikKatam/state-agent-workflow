"""Phase 1 integration tests — code execution in sandboxed containers."""

from __future__ import annotations

import pytest

from conftest import assert_complete, assert_error, await_execute


@pytest.mark.integration
@pytest.mark.phase1
async def test_simple_print_returns_stdout(container_manager):
    """Simple print() returns output in stdout."""
    result = await await_execute(container_manager, "exec-1", "explorer", "print(42)")
    assert_complete(result)
    assert "42" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase1
async def test_multiline_code_execution(container_manager):
    """Multiline code executes and produces correct output."""
    code = "x = 10\ny = 20\nprint(x + y)"
    result = await await_execute(container_manager, "exec-2", "explorer", code)
    assert_complete(result)
    assert "30" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase1
async def test_import_stdlib_modules(container_manager):
    """Standard library modules are importable."""
    code = "import json, os, sys, ast, re\nprint('ok')"
    result = await await_execute(container_manager, "exec-3", "explorer", code)
    assert_complete(result)
    assert "ok" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase1
async def test_code_with_data_processing(container_manager):
    """Data processing code works correctly."""
    code = (
        "data = [3, 1, 4, 1, 5, 9, 2, 6]\n"
        "data.sort()\n"
        "print(data[:3])\n"
    )
    result = await await_execute(container_manager, "exec-4", "explorer", code)
    assert_complete(result)
    assert "[1, 1, 2]" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase1
async def test_exception_handling_in_code(container_manager):
    """User code can catch and handle exceptions."""
    code = (
        "try:\n"
        "    x = 1 / 0\n"
        "except ZeroDivisionError:\n"
        "    print('caught')\n"
    )
    result = await await_execute(container_manager, "exec-5", "explorer", code)
    assert_complete(result)
    assert "caught" in result["stdout"]


@pytest.mark.integration
@pytest.mark.phase1
async def test_syntax_error_returns_error(container_manager):
    """Syntax errors are reported as errors."""
    result = await await_execute(container_manager, "exec-6", "explorer", "def(")
    assert_error(result, "SyntaxError")


@pytest.mark.integration
@pytest.mark.phase1
async def test_runtime_error_returns_error(container_manager):
    """Runtime errors are reported as errors."""
    result = await await_execute(container_manager, "exec-7", "explorer", "1/0")
    assert_error(result, "ZeroDivisionError")
