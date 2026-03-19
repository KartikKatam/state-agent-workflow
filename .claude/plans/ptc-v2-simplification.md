# PTC V2 Simplification Plan

**Status**: Approved by user, ready for implementation
**Date**: 2026-03-07
**Context**: Session analyzed Alibaba OpenSandbox, benchmarked PTC architecture, and determined that enabling bridge networking eliminates the need for the IPC tool dispatch layer. See `PTC-MCP-Hardening.md` in project root for the OpenSandbox comparison that motivated these changes.

---

## Goal

Strip PTC from a protocol-heavy sandbox (8 IPC message types, 15 internal tools, 5 dispatch strategies) down to a **persistent REPL pool manager** with bridge-networked containers. The core value — agents write programs, only print() enters context, 88.5% token reduction — is unchanged. The tool dispatch machinery that is now dead code gets removed.

## Why This Is Correct

With `--network=bridge` and `/workspace:rw`:
- Container code can read/write files directly via Python `open()`
- Container code can make HTTP requests via `requests`, `trafilatura`, etc.
- Container code can run `git` commands via `subprocess`
- Container code can do all analysis with installed packages (ast, networkx, radon, etc.)

The ONLY things that required IPC tool dispatch were operations the container couldn't do natively due to `--network=none` and `/workspace:ro`. Those constraints are being lifted.

For `run_tests` and `run_linter` (which need project dependencies not installed in the container), agents use Claude Code's built-in Bash tool directly — these don't need to go through PTC.

---

## Files to Read First (Current Architecture)

Read these to understand what exists before making changes:

| File | Why |
|------|-----|
| `~/.claude/mcp/ptc-server/server.py` | MCP entry point — 5 tools, output capping, Docker detection |
| `~/.claude/mcp/ptc-server/container_manager.py` | Container lifecycle, REPL pool, sub-agent routing, pip install, network isolation |
| `~/.claude/mcp/ptc-server/ipc_host.py` | Unix socket server, execute/tool_call/complete loop, tool dispatch |
| `~/.claude/mcp/ptc-server/ipc_protocol.py` | 8 NDJSON message types (dataclasses) |
| `~/.claude/mcp/ptc-server/ipc_sandbox.py` | Container-side IPC client, pause-resume via futures |
| `~/.claude/mcp/ptc-server/sandbox_runtime/runtime.py` | Container entry point, message routing, tool_result handling |
| `~/.claude/mcp/ptc-server/sandbox_runtime/executor.py` | Persistent namespace, tool stub injection, memory eviction |
| `~/.claude/mcp/ptc-server/tool_dispatcher.py` | 5 strategies (ptc_tools, subprocess, host_fs, host_network, mcp_proxy) |
| `~/.claude/mcp/ptc-server/tool_registry.py` | Role-to-tool permission enforcement |
| `~/.claude/mcp/ptc-server/config.json` | role_tools, tool_strategies, resource_limits, repl_pool, docker, ipc |
| `~/.claude/mcp/ptc-server/event_logger.py` | JSONL observability (11 event types) |
| `~/.claude/mcp/ptc-server/Dockerfile` | Base image: python:3.11-slim + git + pytest + ruff |
| `~/.claude/mcp/ptc-server/build-image.sh` | Builds base image |
| `~/.claude/mcp/ptc-server/ptc_tools/` | 7 modules implementing 15 tools (files, search, git, testing, tokens, network, _base) |

Also read for context:
| File | Why |
|------|-----|
| `~/personal/agentic_workflow/PTC-MCP-Hardening.md` | The 4 hardening upgrades doc (pre-built images, TTL cleanup, gVisor, readiness detection) — some of these are incorporated into this plan |
| `~/personal/agentic_workflow/PTC-RESUME.md` | Original architecture summary for orientation |

---

## Phase 1: Delete Tool Dispatch Layer

### 1A. Delete files entirely

| File | Lines | Reason |
|------|-------|--------|
| `~/.claude/mcp/ptc-server/tool_dispatcher.py` | 213 | No tools to dispatch |
| `~/.claude/mcp/ptc-server/tool_registry.py` | 60 | No role-tool permissions needed |
| `~/.claude/mcp/ptc-server/ipc_sandbox.py` | ~80 | Container-side IPC client for tool calls — no tool calls |
| `~/.claude/mcp/ptc-server/ptc_tools/__init__.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/_base.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/files.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/search.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/git.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/testing.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/tokens.py` | — | Entire module deleted |
| `~/.claude/mcp/ptc-server/ptc_tools/network.py` | — | Entire module deleted |

### 1B. Remove tool dispatch imports from remaining files

- `server.py`: Remove `from tool_dispatcher import ToolDispatcher` and `from tool_registry import ToolRegistry`. Remove `registry` and `tool_dispatcher` instantiation (lines 81-86). Pass `None` or remove the `registry` param from ContainerManager.
- `ipc_host.py`: Remove `tool_dispatcher` parameter. Remove `_handle_tool_call()` method. Remove all tool-related imports from `ipc_protocol`.
- `container_manager.py`: Remove `registry` parameter from `__init__`. The IpcHost no longer needs a tool_dispatcher.

---

## Phase 2: Simplify IPC Protocol

### 2A. Reduce ipc_protocol.py from 8 to 5 message types

**Keep:**
- `ExecuteMsg` (host → container: send code)
- `CompleteMsg` (container → host: return stdout/stderr/namespace)
- `ErrorMsg` (container → host: execution failed)
- `PingMsg` / `PongMsg` (health check)

**Delete:**
- `ToolCallMsg` — no tool calls
- `ToolResultMsg` — no tool responses
- `CancelMsg` — handle timeout by killing process instead

Also delete `new_call_id()` — only `new_exec_id()` is needed.

### 2B. Simplify ipc_host.py

The `send_execute()` method becomes a simple send/receive — no `_read_loop`, no pending tool tasks, no semaphore, no max_tool_calls enforcement.

Current `_read_loop()` (lines 133-188) handles `tool_call`, `complete`, `error` message types with concurrent task dispatch. Replace with:

```python
async def send_execute(self, exec_id: str, code: str, timeout: float) -> dict:
    """Send code, wait for complete/error response."""
    msg = ExecuteMsg(exec_id=exec_id, code=code)
    self._writer.write(encode(msg))
    await self._writer.drain()

    self._event_logger.log_exec_start(exec_id, exec_id, len(code), "await " in code)
    start = time.monotonic()

    try:
        line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        if not line:
            return {"type": "error", "exec_id": exec_id, "message": "Connection closed"}
        result = decode(line)
    except asyncio.TimeoutError:
        result = {"type": "error", "exec_id": exec_id, "message": "Timeout"}

    duration_ms = int((time.monotonic() - start) * 1000)
    if result.get("type") == "complete":
        self._event_logger.log_exec_complete(
            exec_id, exec_id, duration_ms,
            len(result.get("stdout", "")), 0,
            result.get("namespace_keys", []),
            result.get("namespace_size_kb", 0),
        )
    else:
        self._event_logger.log_exec_error(
            exec_id, exec_id, result.get("message", "unknown"), "", duration_ms,
        )
    return result
```

Remove entirely: `_read_loop()`, `_handle_tool_call()`, `_per_tool_timeout`, `_max_tool_calls` fields.

### 2C. Simplify sandbox_runtime/runtime.py

Remove all tool_result routing and IpcClient dependency. The runtime loop becomes:

```python
async def run(reader, writer):
    executor = Executor()  # No ipc_client, no allowed_tools

    while True:
        line = await reader.readline()
        if not line:
            break
        data = decode(line)

        if data["type"] == "execute":
            stdout, stderr, rc, keys, size = await executor.execute(data["code"])
            if rc == 0:
                resp = CompleteMsg(exec_id=data["exec_id"], stdout=stdout, stderr=stderr,
                                  return_code=rc, namespace_keys=keys, namespace_size_kb=size)
            else:
                resp = ErrorMsg(exec_id=data["exec_id"], message=stderr, traceback=stderr)
            writer.write(encode(resp))
            await writer.drain()

        elif data["type"] == "ping":
            writer.write(encode(PongMsg()))
            await writer.drain()
```

Remove: `from ipc_sandbox import IpcClient`, `ipc_client` instantiation, `tool_result` handling, `cancel` handling.

Remove the `--tools` CLI argument from `parse_args()` — no longer needed.

### 2D. Simplify sandbox_runtime/executor.py

Remove `__init__` parameters (`ipc_client`, `allowed_tools`). Delete `_setup_tool_stubs()` entirely.

New `__init__`:
```python
def __init__(self) -> None:
    self._namespace: dict = {}
```

Keep everything else: `execute()`, `_execute_async()`, `_user_keys()`, `_namespace_size_kb()`, `_evict_if_over_limit()`.

**Bug fix while here**: `_evict_if_over_limit()` is never called (benchmark Bug D3). Add call at end of `execute()`:
```python
# After successful execution, check memory pressure
self._evict_if_over_limit()
```

---

## Phase 3: Enable Bridge Networking + Read-Write Mount

### 3A. container_manager.py: Change container creation

In `_create_container()` (line 309 area):

1. Change network to bridge:
```python
"--network", "bridge",
```

2. Change workspace mount to read-write:
```python
"-v", f"{self._project_root}:/workspace:rw",
```

3. Remove `_isolate_network()` method entirely (lines 361-376).

4. Remove `_install_role_packages()` method entirely (lines 378-443) — using pre-built role images (see Phase 5).

5. Remove `ROLE_PACKAGES` dict entirely (lines 22-109).

6. Remove the pip install call and network isolation call from `get_or_create_repl()` (line 190-191):
```python
# Before:
container = await self._create_container(role)
await self._install_role_packages(container.container_name, role)
await self._isolate_network(container.container_name)
return await self._start_repl_in_container(container, agent_id)

# After:
container = await self._create_container(role)
return await self._start_repl_in_container(container, agent_id)
```

### 3B. container_manager.py: Use role-specific images

Update `_create_container()` to use `ptc-{role}:latest` image:

```python
image = f"ptc-{role}:latest"
```

### 3C. container_manager.py: Remove tool-related parameters

- Remove `registry` parameter from `__init__` (currently receives `tool_dispatcher`)
- Update `_start_repl_in_container()` to not pass `--tools` argument (lines 562-573)
- IpcHost constructor no longer takes `tool_dispatcher` parameter

### 3D. container_manager.py: Fix REPL readiness (from Hardening doc)

Replace `await asyncio.sleep(0.5)` (line 583) with poll-based readiness:

```python
async def _wait_for_repl_ready(self, ipc: IpcHost, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    interval = 0.05
    while time.monotonic() < deadline:
        try:
            healthy = await ipc.health_check(timeout=0.5)
            if healthy:
                return
        except Exception:
            pass
        await asyncio.sleep(interval)
        interval = min(interval * 1.5, 0.2)
    raise RuntimeError(f"REPL not ready within {timeout_seconds}s")
```

---

## Phase 4: Simplify Config and Server

### 4A. config.json — strip to essentials

```json
{
    "resource_limits": {
        "memory_mb": 1024,
        "cpu_cores": 1,
        "timeout_seconds": 60,
        "max_containers": 8,
        "disk_mb": 512,
        "max_output_bytes": 65536,
        "idle_timeout_seconds": 300,
        "container_ttl_seconds": 600
    },
    "repl_pool": {
        "max_repls_per_container": 6,
        "repl_start_timeout_seconds": 5
    },
    "docker": {
        "image": "ptc-sandbox:latest",
        "role_image_pattern": "ptc-{role}:latest",
        "network_mode": "bridge",
        "container_prefix": "ptc",
        "runtime": "runc"
    },
    "ipc": {
        "socket_dir": "/tmp/ptc-ipc"
    },
    "observability": {
        "log_file": "~/.claude/logs/ptc-events.jsonl",
        "debug_log_code": false,
        "result_preview_chars": 200
    }
}
```

Removed: `role_tools`, `tool_strategies`, `output_formats`, `socket_permissions`, `connect_timeout_seconds`, `tool_call_timeout_seconds`, `pip_install_timeout_seconds`, `max_tool_calls_per_execution`, `repl_idle_timeout_seconds`.

### 4B. server.py — update imports and docstring

Remove imports:
```python
# Delete these:
from tool_dispatcher import ToolDispatcher
from tool_registry import ToolRegistry
```

Remove instantiation (lines 81-92):
```python
# Delete registry and tool_dispatcher creation
# Simplify ContainerManager instantiation — no registry param
container_manager = ContainerManager(
    config=config,
    project_root=PROJECT_ROOT,
    event_logger=event_logger,
)
```

Update `ptc_execute` docstring to reflect new capabilities (network, read-write workspace, no tool stubs).

### 4C. server.py — add TTL cleanup startup (from Hardening doc)

Start the background TTL cleanup loop when MCP server starts.

---

## Phase 5: Pre-Built Role Images (from Hardening doc)

### 5A. Create Dockerfile per role

Create 6 new Dockerfiles in `~/.claude/mcp/ptc-server/`:

- `Dockerfile.explorer` — tree-sitter, networkx, radon, vulture, pandas, gitpython, pydriller, tiktoken, etc.
- `Dockerfile.coder` — pyinstrument, pympler, radon, coverage, bandit, unidiff, tiktoken, etc.
- `Dockerfile.auditor` — radon, vulture, coverage, bandit, unidiff, networkx, pydriller, tiktoken, etc.
- `Dockerfile.tester` — hypothesis, coverage, pyinstrument, big-o, memray, radon, tiktoken, etc.
- `Dockerfile.researcher` — trafilatura, html2text, markdownify, readability-lxml, requests, pypdf, pandas, rapidfuzz, tiktoken, etc.
- `Dockerfile.strategist` — networkx, radon, cognitive-complexity, tiktoken

Each follows the pattern:
```dockerfile
FROM ptc-sandbox:latest
USER root
RUN pip install --no-cache-dir <packages>
USER ptcuser
```

### 5B. Replace build-image.sh with build-images.sh

New script that builds base + all 6 role images.

### 5C. Add `requests` to base or researcher image

The researcher role needs `requests` (or `httpx`) for web fetching now that bridge networking is enabled. Add to `Dockerfile.researcher`. Consider adding to base image since any role may want network access.

---

## Phase 6: Update Event Logger

### 6A. Remove tool-call events

The event logger has methods for tool call lifecycle that become dead code:
- `log_tool_call_start()`
- `log_tool_call_complete()`
- `log_tool_call_error()`
- `log_tool_call_denied()`

Delete these 4 methods. Also delete `log_pip_install_start()` and `log_pip_install_done()` since pip install is gone.

Keep all container lifecycle and execution lifecycle events.

---

## Phase 7: Update Tests

### 7A. Delete test files for removed components

| File | Reason |
|------|--------|
| `tests/ptc/test_ptc_tools.py` | All 15 tool tests — tools deleted |
| `tests/ptc/test_tool_dispatcher.py` | Dispatcher deleted |
| `tests/ptc/test_tool_registry.py` | Registry deleted |

### 7B. Update test files for simplified components

| File | Changes |
|------|---------|
| `tests/ptc/conftest.py` | Remove role_tools fixtures, remove tool-related fixtures, update SAMPLE_CONFIG to match new config.json |
| `tests/ptc/test_ipc_protocol.py` | Remove ToolCallMsg and ToolResultMsg tests, remove CancelMsg test, keep ExecuteMsg/CompleteMsg/ErrorMsg/Ping/Pong |
| `tests/ptc/test_ipc_host.py` | Rewrite to test simple send_execute→complete flow, remove all tool dispatch tests |
| `tests/ptc/test_sandbox_runtime.py` | Remove tool stub tests, test executor without ipc_client/tools params |
| `tests/ptc/test_event_logger.py` | Remove tool_call event tests, keep container/execution event tests |
| `tests/ptc/test_ptc_execute.py` | Update to match new server.py (no registry/dispatcher) |
| `tests/ptc/test_container_manager.py` | Remove pip install tests, remove network isolation tests |

### 7C. Update integration tests

| File | Changes |
|------|---------|
| `tests/ptc/integration/test_phase1_tool_calling.py` | **Rewrite entirely** — test direct Python (open(), requests.get(), subprocess) instead of await tool() |
| `tests/ptc/integration/test_phase2_role_diversity.py` | Update to test that packages are available, not that tools are routed |
| `tests/ptc/integration/test_phase2_concurrent_execution.py` | Update code strings to use direct Python instead of await read_file() |
| `tests/ptc/integration/test_benefit_measurement.py` | Update to measure token savings with direct Python pattern |
| `tests/ptc/integration/test_observability.py` | Update code strings |

---

## Phase 8: Update Documentation and Skills

### 8A. Critical documentation (agents read these at startup)

| File | Changes |
|------|---------|
| `skills/ptc-sandbox/SKILL.md` | Rewrite — show direct Python patterns instead of await tool() |
| `skills/ptc-sandbox/references/tool-calling-patterns.md` | **Rename** to `code-patterns.md`, rewrite with open()/requests/subprocess examples |
| `skills/ptc-sandbox/references/anti-patterns.md` | Update examples |
| `skills/ptc-sandbox/role-specs/*.md` | Update all 6 role specs — remove "When to Use PTC" tool-calling recipes, replace with direct Python recipes |

### 8B. Context packets and plans (stale but functional)

| File | Action |
|------|--------|
| `.claude/context/_codebase.json` | Update ptc-server file list |
| `.claude/context/ptc-context.json` | Rewrite — remove tool schemas, update architecture |
| `.claude/designs/ptc.md` | Archive as `ptc-v1-design.md`, write new v2 design |
| `.claude/plans/ptc-plan.json` | Archive — this plan supersedes it |

### 8C. Non-critical (update when convenient)

| File | Action |
|------|--------|
| `ptc-ipc-consolidated.md` | Archive as v1 reference |
| `ptc-skill-integration-analysis.md` | Update code examples |
| `PTC-RESUME.md` | Rewrite to reflect v2 architecture |
| `PTC-MCP-Hardening.md` | Mark Upgrades 1, 2, 4 as incorporated into this plan |
| `hooks/subagent_start.py` (lines 395, 397) | Remove tool lists from role configs |

---

## Verification Checklist

After all phases:

- [ ] `ptc_execute` works end-to-end: send Python code, get stdout back
- [ ] Namespace persists across calls (variable set in call 1 accessible in call 2)
- [ ] Container code can read/write `/workspace` files directly
- [ ] Container code can make HTTP requests (bridge networking)
- [ ] Container code can run git commands via subprocess
- [ ] Container code can import role-specific packages (networkx, radon, etc.)
- [ ] Sub-agent routing works (sub-agent gets REPL in parent's container)
- [ ] REPL pool enforces max 6 REPLs per container
- [ ] Idle container cleanup works (TTL-based)
- [ ] Health check (ping/pong) works
- [ ] Event logger captures execution events
- [ ] Memory eviction fires at 512MB namespace
- [ ] Output capped at 64KB
- [ ] Pre-built role images build successfully
- [ ] All remaining tests pass

---

## Line Count Impact

| Category | Before | After | Delta |
|----------|--------|-------|-------|
| ptc-server Python files | 14 | 7 | -7 files |
| ptc-server total lines | ~2,400 | ~800 | -1,600 lines |
| IPC message types | 8 | 5 | -3 |
| Config entries | 30+ | 15 | -15+ |
| Internal tools | 15 | 0 | -15 |
| Dispatch strategies | 5 | 0 | -5 |
| Test files affected | 15 | 15 | rewrite/delete |
| Doc files affected | ~30 | ~30 | update |
