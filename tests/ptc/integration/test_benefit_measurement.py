"""Tests for PTC benefit measurement -- token reduction, latency, qualitative.

PTC V2 uses direct Python (open(), os.walk, subprocess) instead of tool stubs.
Token savings come from batching multiple file operations in a single code
execution rather than making individual MCP round-trips.
"""

from __future__ import annotations

import time

import pytest

from benefit_measurement import (
    assess_code_sophistication,
    count_mcp_equivalent_tokens,
    count_ptc_tokens,
    token_reduction_pct,
)

pytestmark = [pytest.mark.integration, pytest.mark.benefit]


async def test_token_reduction_single_file(container_manager):
    """PTC uses fewer tokens than MCP for single file read."""
    mgr = container_manager
    code = "data = open('/workspace/_test_server.py').read(); print(f'Lines: {len(data.splitlines())}')"
    await mgr.get_or_create_repl("benefit-single-1", "explorer")
    result = await mgr.execute_code("benefit-single-1", code, 30)

    ptc_tokens = count_ptc_tokens(code, result)
    mcp_tokens = count_mcp_equivalent_tokens(
        [{"tool": "read_file", "estimated_content_size": 5000}]
    )
    reduction = token_reduction_pct(ptc_tokens, mcp_tokens)

    print(f"Single file - PTC: {ptc_tokens}, MCP: {mcp_tokens}, Reduction: {reduction:.1f}%")
    assert reduction > 0, f"Expected token reduction, got {reduction:.1f}%"


async def test_token_reduction_multi_file(container_manager):
    """PTC uses 50%+ fewer tokens for multi-file reads."""
    mgr = container_manager
    code = """
import os
files = [f for f in os.listdir('/workspace') if f.endswith('.py')][:5]
total_lines = 0
for f in files:
    data = open(f'/workspace/{f}').read()
    total_lines += len(data.splitlines())
print(f"Total lines across {len(files)} files: {total_lines}")
"""
    await mgr.get_or_create_repl("benefit-multi-1", "explorer")
    result = await mgr.execute_code("benefit-multi-1", code, 60)

    ptc_tokens = count_ptc_tokens(code, result)
    mcp_tokens = count_mcp_equivalent_tokens(
        [{"tool": "read_file", "estimated_content_size": 5000} for _ in range(5)]
    )
    reduction = token_reduction_pct(ptc_tokens, mcp_tokens)

    print(f"Multi-file - PTC: {ptc_tokens}, MCP: {mcp_tokens}, Reduction: {reduction:.1f}%")
    assert reduction > 50, f"Expected >50% reduction, got {reduction:.1f}%"


async def test_token_reduction_aggregation(container_manager):
    """PTC uses 70%+ fewer tokens for aggregation across many files."""
    mgr = container_manager
    code = """
import json, os
py_files = [f for f in os.listdir('/workspace') if f.endswith('.py')]
summary = {"total_files": len(py_files)}
for f in py_files[:10]:
    data = open(f'/workspace/{f}').read()
    summary[f] = len(data.splitlines())
print(json.dumps(summary))
"""
    await mgr.get_or_create_repl("benefit-agg-1", "explorer")
    result = await mgr.execute_code("benefit-agg-1", code, 60)

    ptc_tokens = count_ptc_tokens(code, result)
    mcp_tokens = count_mcp_equivalent_tokens(
        [
            {"tool": "list_dir", "estimated_content_size": 1000},
            *[{"tool": "read_file", "estimated_content_size": 5000} for _ in range(10)],
        ]
    )
    reduction = token_reduction_pct(ptc_tokens, mcp_tokens)

    print(f"Aggregation - PTC: {ptc_tokens}, MCP: {mcp_tokens}, Reduction: {reduction:.1f}%")
    assert reduction > 70, f"Expected >70% reduction, got {reduction:.1f}%"


async def test_latency_single_op(container_manager):
    """Record PTC latency for single file read."""
    mgr = container_manager
    await mgr.get_or_create_repl("benefit-lat-1", "explorer")

    start = time.monotonic()
    result = await mgr.execute_code(
        "benefit-lat-1",
        "data = open('/workspace/_test_server.py').read(); print(len(data))",
        30,
    )
    latency = time.monotonic() - start

    print(f"Single op latency: {latency:.3f}s")
    assert result.get("type") == "complete"


async def test_latency_parallel_ops(container_manager):
    """Record PTC latency for batch file reads in a single execution."""
    mgr = container_manager
    await mgr.get_or_create_repl("benefit-par-1", "explorer")

    code = """
import os
files = [f for f in os.listdir('/workspace') if f.endswith('.py')][:3]
results = {f: len(open(f'/workspace/{f}').read()) for f in files}
print(f"Read {len(results)} files")
"""
    start = time.monotonic()
    result = await mgr.execute_code("benefit-par-1", code, 30)
    latency = time.monotonic() - start

    print(f"Batch ops latency: {latency:.3f}s")
    assert result.get("type") == "complete"


async def test_qualitative_sophistication():
    """Explorer code with multiple file operations scores high on sophistication."""
    code = """
import json, os
py_files = [f for f in os.listdir('/workspace') if f.endswith('.py')]
stats = {}
for f in py_files[:5]:
    data = open(f'/workspace/{f}').read()
    lines = data.splitlines()
    imports = [l for l in lines if l.startswith('import') or l.startswith('from')]
    stats[f] = {"lines": len(lines), "imports": len(imports)}
summary = {"files_analyzed": len(stats), "total_lines": sum(s["lines"] for s in stats.values())}
print(json.dumps(summary, indent=2))
"""
    score = assess_code_sophistication(code)
    print(f"Sophistication score: {score}")
    assert score.get("total_score", 0) >= 3, f"Expected score >= 3, got {score}"
