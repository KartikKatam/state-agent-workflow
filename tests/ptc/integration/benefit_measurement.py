from __future__ import annotations

import re
import time
from typing import Any, Callable


# -- Token estimation ---------------------------------------------------------


def count_ptc_tokens(code: str, result: dict[str, Any]) -> int:
    """Estimate tokens in PTC code sent + stdout returned.

    Uses the len(text) // 4 approximation (~1 token per 4 chars).
    """
    code_chars = len(code)
    result_chars = len(str(result.get("stdout", ""))) + len(str(result.get("stderr", "")))
    return (code_chars + result_chars) // 4


def count_mcp_equivalent_tokens(tool_calls: list[dict[str, Any]]) -> int:
    """Estimate tokens for N separate MCP round-trips.

    Each MCP call has ~2000 tokens of protocol overhead (tool schema,
    request framing, response framing) plus the actual content.
    """
    MCP_OVERHEAD_PER_CALL = 2000
    total = 0
    for call in tool_calls:
        content_tokens = len(str(call)) // 4
        total += MCP_OVERHEAD_PER_CALL + content_tokens
    return total


def token_reduction_pct(ptc_tokens: int, mcp_tokens: int) -> float:
    """Percentage reduction: (mcp - ptc) / mcp * 100.

    Returns 0.0 if mcp_tokens is zero to avoid division by zero.
    """
    if mcp_tokens == 0:
        return 0.0
    return (mcp_tokens - ptc_tokens) / mcp_tokens * 100.0


# -- Latency measurement -----------------------------------------------------


async def measure_ptc_latency(
    execute_fn: Callable[..., Any],
    agent_id: str,
    role: str,
    code: str,
    timeout: int = 60,
) -> tuple[dict[str, Any], float]:
    """Measure wall-clock seconds for a PTC execution.

    Calls ``await execute_fn(agent_id, role, code, timeout=timeout)`` and
    returns the (result_dict, elapsed_seconds) tuple.  The *execute_fn*
    should be an async callable (e.g. ``await_execute`` from conftest).
    """
    start = time.perf_counter()
    result = await execute_fn(agent_id, role, code, timeout=timeout)
    elapsed = time.perf_counter() - start
    return result, round(elapsed, 4)


# -- Code sophistication -----------------------------------------------------

_FILE_IO_RE = re.compile(r"\bopen\s*\(")
_LOOP_RE = re.compile(r"\b(for|while)\b")
_AGGREGATION_RE = re.compile(r"\b(sum|mean|groupby|aggregate|reduce|count)\b", re.IGNORECASE)
_IMPORT_RE = re.compile(r"^\s*(import |from \S+ import )", re.MULTILINE)
_SUBPROCESS_RE = re.compile(r"\bsubprocess\.(run|Popen|check_output)\b")


def assess_code_sophistication(code: str) -> dict[str, Any]:
    """Assess how sophisticated a PTC code snippet is.

    Counts:
    - file_io: occurrences of ``open()`` for file operations
    - loops: ``for`` / ``while`` keywords
    - aggregation: sum / mean / groupby / aggregate / reduce / count
    - library_imports: import statements
    - subprocess: subprocess.run / Popen / check_output calls

    Returns a dict with individual counts and a ``total_score`` (simple sum).
    """
    file_io = len(_FILE_IO_RE.findall(code))
    loops = len(_LOOP_RE.findall(code))
    aggregation = len(_AGGREGATION_RE.findall(code))
    library_imports = len(_IMPORT_RE.findall(code))
    subprocess_calls = len(_SUBPROCESS_RE.findall(code))

    return {
        "file_io": file_io,
        "loops": loops,
        "aggregation": aggregation,
        "library_imports": library_imports,
        "subprocess": subprocess_calls,
        "total_score": file_io + loops + aggregation + library_imports + subprocess_calls,
    }
