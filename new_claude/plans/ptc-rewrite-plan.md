# PTC Implementation: True Pause-Resume Programmatic Tool Calling via ipybox

**Status:** Plan complete, not yet implemented
**Date:** 2026-02-28

---

## Context

The current PTC implementation is wrong — 15 hardcoded MCP tools that are just structured API wrappers. Agents call fixed-signature tools, get raw results dumped into their context window, and never write code. This is the opposite of programmatic tool calling.

This plan implements true PTC using **ipybox** (Gradion AI) as the container runtime. ipybox provides IPython kernels inside Docker containers with native top-level `await`, persistent state, and built-in MCP tool call interception. This eliminates the need for custom REPL bootstraps, sentinel protocols, and fragile async wrapping.

The old implementation is removed entirely. No backward compatibility, no fallbacks to the old approach.

---

## How Anthropic's PTC Works (What We're Mimicking)

1. Agent writes Python code with `await tool_name(params)`
2. Code execution PAUSES at each `await` tool call
3. Host fulfills the tool (reads file, runs tests, calls API)
4. Result injected back into sandbox, code RESUMES
5. Intermediate results stay in sandbox — never enter agent context
6. Only `print()` output returns to agent
7. Container persists — state survives across calls

**Token savings:** 37-93% reduction. A 200KB dataset becomes a 1KB summary.

---

## Architecture

```
Agent (Claude Code teammate)
  |
  | calls ptc_execute(agent_id, role, code) via MCP
  v
PTC MCP Server (server.py)
  |
  | imports ipybox
  | manages per-agent ExecutionContainer instances
  |
  | sends code via ExecutionClient.execute(code)
  v
ipybox ExecutionContainer (Docker)
  |
  | IPython kernel executes code
  | top-level await works natively (autoawait)
  | namespace persists across calls
  |
  | code calls: data = await read_file(path="src/main.py")
  |
  | ipybox intercepts tool call via approval gate
  v
PTC MCP Server receives tool call request
  |
  | checks role permissions (ToolRegistry)
  | dispatches to tool_dispatch.py
  | routes to existing fallback() function
  | returns result to container
  v
IPython kernel receives result
  |
  | code continues executing
  | more awaits → more pause/resume cycles
  | code completes
  |
  | print() output captured
  v
PTC MCP Server returns stdout to agent
```

---

## Why ipybox

| Problem | Custom REPL (old plan) | ipybox |
|---------|----------------------|--------|
| Top-level `await` | Fragile `"await " in code` detection + `async def` wrapping | IPython autoawait — native, battle-tested |
| Container management | Custom Docker subprocess management | `ExecutionContainer` async context manager |
| State persistence | Custom `_namespace` dict + `exec()` | IPython kernel — standard, handles edge cases |
| Tool call interception | Custom sentinel protocol (5 sentinels, bidirectional stdin/stdout) | Built-in approval gate via ResourceClient |
| Code execution | `docker exec -i` with pipe management | `ExecutionClient.execute()` — clean async API |
| Port-based communication | Not available | executor_port + resource_port per container |
| Multi-model support | Tied to specific code generation patterns | Any model that generates Python works |

---

## File Structure

```
~/.claude/mcp/ptc-server/
  server.py                      # REWRITTEN: ipybox-based, single ptc_execute tool
  container_manager.py           # NEW: per-agent ipybox container lifecycle
  tool_dispatch.py               # NEW: host-side tool dispatcher (routes to fallback functions)
  tool_registry.py               # MODIFIED: role → allowed tools mapping
  config.json                    # REWRITTEN: ipybox config, role-tools, resource limits
  requirements.txt               # UPDATED: add ipybox dependency
  tool_implementations/          # KEPT: only fallback() functions (remove make_kernel_code)
    read_file.py
    list_dir.py
    search_code.py
    ... (all 15)

REMOVED:
  kernel_manager.py              # Replaced by container_manager.py using ipybox
  repl/                          # Not needed — IPython handles everything
  tool_implementations/*/make_kernel_code()  # Removed from each module

~/personal/agentic_workflow/tests/ptc/
  test_container_manager.py      # NEW: ipybox container lifecycle tests
  test_tool_dispatch.py          # NEW: host-side dispatch tests
  test_ptc_execute.py            # NEW: end-to-end tests
  conftest.py                    # REWRITTEN: ipybox-based fixtures

REMOVED:
  test_kernel_manager.py         # Old Docker subprocess tests
  test_server.py                 # Old 15-tool server tests
  test_tools.py                  # Old make_kernel_code tests
```

---

## Component 1: Container Manager (`container_manager.py`)

Manages per-agent ipybox containers using ipybox as a library.

```python
"""Per-agent ipybox container lifecycle management.

Each agent gets its own ExecutionContainer with:
- Persistent IPython kernel (state survives across calls)
- Role-scoped tool stubs (generated via ResourceClient)
- Isolated filesystem (project mounted read-only at /workspace)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ipybox import ExecutionClient, ExecutionContainer, ResourceClient


@dataclass
class AgentContainer:
    """Tracks an agent's ipybox container and clients."""
    agent_id: str
    role: str
    container: ExecutionContainer
    exec_client: ExecutionClient
    resource_client: ResourceClient
    tool_names: list[str] = field(default_factory=list)
    total_executions: int = 0
    total_tool_calls: int = 0


class ContainerManager:
    """Manages per-agent ipybox containers.

    Each agent gets its own container with persistent state.
    Containers are created lazily on first ptc_execute call.
    """

    def __init__(self, config: dict, project_root: str, registry):
        self._config = config
        self._project_root = project_root
        self._registry = registry
        self._agents: dict[str, AgentContainer] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_create(self, agent_id: str, role: str) -> AgentContainer:
        """Get existing container or create new one for agent."""
        if agent_id in self._agents:
            return self._agents[agent_id]

        lock = self._locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            if agent_id in self._agents:
                return self._agents[agent_id]
            return await self._create_agent_container(agent_id, role)

    async def _create_agent_container(self, agent_id: str, role: str) -> AgentContainer:
        """Create a new ipybox container for an agent."""
        docker_config = self._config.get("docker", {})
        limits = self._config.get("resource_limits", {})

        container = ExecutionContainer(
            tag=docker_config.get("image", "ghcr.io/gradion-ai/ipybox"),
            # ipybox handles Docker flags internally
            # Project mounted read-only for code access
        )
        await container.__aenter__()

        exec_client = ExecutionClient(port=container.executor_port)
        await exec_client.__aenter__()

        resource_client = ResourceClient(port=container.resource_port)
        await resource_client.__aenter__()

        # Generate tool stubs for this role's allowed tools
        allowed_tools = self._registry.get_tools_for_role(role)
        tool_names = await self._generate_tool_stubs(
            resource_client, allowed_tools
        )

        # Mount project directory
        await exec_client.execute(
            f"import sys; sys.path.insert(0, '/workspace')"
        )

        agent = AgentContainer(
            agent_id=agent_id,
            role=role,
            container=container,
            exec_client=exec_client,
            resource_client=resource_client,
            tool_names=tool_names,
        )
        self._agents[agent_id] = agent
        return agent

    async def execute(self, agent_id: str, code: str) -> dict:
        """Execute code in the agent's container."""
        agent = self._agents.get(agent_id)
        if not agent:
            return {"error": f"No container for agent {agent_id}"}

        lock = self._locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            result = await agent.exec_client.execute(code)
            agent.total_executions += 1
            return {
                "stdout": result.text,
                "exit_code": 0,
            }

    async def stop_agent(self, agent_id: str):
        """Stop and cleanup an agent's container."""
        agent = self._agents.pop(agent_id, None)
        if agent:
            await agent.exec_client.__aexit__(None, None, None)
            await agent.resource_client.__aexit__(None, None, None)
            await agent.container.__aexit__(None, None, None)

    async def stop_all(self):
        """Stop all agent containers."""
        for agent_id in list(self._agents.keys()):
            await self.stop_agent(agent_id)
```

**Note:** The exact ipybox API for mounting volumes, setting resource limits, and configuring the tool approval gate needs to be verified during implementation. The above is the structural design — specific ipybox constructor arguments may differ.

---

## Component 2: Tool Dispatch (`tool_dispatch.py`)

Host-side tool execution. Routes tool calls to existing `fallback()` functions. Unchanged from previous plan — this is the execution backend regardless of container runtime.

```python
"""Host-side tool dispatcher.

When ipybox's approval gate fires a tool call from the container,
this module executes it on the host using existing fallback() functions.
"""

from tool_implementations import (
    read_file, list_dir, search_code, analyze_imports,
    count_tokens, run_tests, run_linter, write_file,
    git_diff_summary, coverage_summary, analyze_structure,
    web_fetch_summary, mcp_call_summary, diff_summary, list_changes,
)

_TOOL_MODULES = {
    "read_file": read_file,
    "write_file": write_file,
    "list_dir": list_dir,
    "search_code": search_code,
    # ... all 15
}

async def dispatch(tool_name: str, params: dict, project_root: str) -> dict:
    """Execute a tool on the host. Returns result dict."""
    module = _TOOL_MODULES.get(tool_name)
    if not module:
        raise ValueError(f"Unknown tool: {tool_name}")
    params["project_root"] = project_root
    return module.fallback(**params)
```

---

## Component 3: Server (`server.py`)

Single `ptc_execute` MCP tool. No other tools exposed.

```python
"""PTC MCP Server — True Programmatic Tool Calling via ipybox.

Exposes a single tool: ptc_execute(agent_id, role, code)
Agents write Python code. Code calls tools via await.
Intermediate results stay in container. Only print() returns.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ptc-sandbox")
manager = ContainerManager(config, PROJECT_ROOT, registry)


@mcp.tool()
async def ptc_execute(agent_id: str, role: str, code: str, timeout: int = 0) -> str:
    """Execute Python code in a persistent sandboxed container.

    The container has a persistent namespace — variables from previous
    calls are available. Use `await tool_name(params)` to call tools.
    Available tools depend on the agent's role.

    Only print() output is returned. Intermediate tool results stay
    in the container for token efficiency.

    Args:
        agent_id: Unique agent identifier.
        role: Agent role (explorer, coder, tester, auditor, strategist, researcher).
        code: Python code to execute. May contain top-level await calls.
        timeout: Optional timeout in seconds (0 = use default).
    """
    # Ensure container exists for this agent
    agent = await manager.get_or_create(agent_id, role)

    # Execute code in IPython kernel
    result = await manager.execute(agent_id, code)
    return json.dumps(result)


@mcp.tool()
async def ptc_shutdown(agent_id: str) -> str:
    """Shut down the container for a specific agent."""
    await manager.stop_agent(agent_id)
    return json.dumps({"status": "stopped", "agent_id": agent_id})


@mcp.tool()
async def ptc_status(agent_id: str) -> str:
    """Get container status for an agent."""
    agent = manager._agents.get(agent_id)
    if not agent:
        return json.dumps({"status": "not_running"})
    return json.dumps({
        "status": "running",
        "role": agent.role,
        "total_executions": agent.total_executions,
        "total_tool_calls": agent.total_tool_calls,
        "available_tools": agent.tool_names,
    })
```

---

## Component 4: Cleaned Tool Implementations

Remove `make_kernel_code()` from all 15 modules. Keep only `fallback()`.

**Before (each module had two functions):**
```python
def make_kernel_code(path, encoding="utf-8"):
    """Generate Python code string for container execution."""
    return f'''...'''  # DELETE THIS

def fallback(path, project_root, encoding="utf-8"):
    """Execute on host."""
    # KEEP THIS — used by tool_dispatch.py
    return {...}
```

**After (each module has one function):**
```python
def fallback(path, project_root, encoding="utf-8"):
    """Execute on host. Called by tool_dispatch.py."""
    return {...}
```

---

## Component 5: Config (`config.json`)

```json
{
  "role_tools": {
    "explorer": ["read_file", "list_dir", "search_code", "analyze_imports", "count_tokens"],
    "researcher": ["web_fetch_summary", "mcp_call_summary"],
    "coder": ["read_file", "write_file", "run_tests", "run_linter", "git_diff_summary"],
    "tester": ["read_file", "run_tests", "coverage_summary"],
    "auditor": ["read_file", "search_code", "diff_summary", "list_changes"],
    "strategist": ["read_file", "analyze_structure"]
  },
  "resource_limits": {
    "memory_mb": 512,
    "cpu_cores": 1,
    "timeout_seconds": 120,
    "max_containers": 8
  },
  "docker": {
    "image": "ghcr.io/gradion-ai/ipybox"
  }
}
```

No REPL config, no sentinel config, no fallback config, no pre-warm. Clean.

---

## Token Efficiency (Unchanged)

### Explorer: 10 files + import analysis
| Approach | Context Used | MCP Round Trips |
|----------|-------------|-----------------|
| Old (15 tools) | ~22KB | 11 |
| PTC | ~1.6KB | 1 |
| **Savings** | **93%** | **91%** |

### Coder: TDD cycle
| Approach | Context Used | MCP Round Trips |
|----------|-------------|-----------------|
| Old (15 tools) | ~1.9KB | 5 |
| PTC | ~1.1KB | 1 |
| **Savings** | **42%** | **80%** |

---

## Parallel Agent Support

Each agent gets its own `ExecutionContainer`. ipybox handles Docker container creation, port allocation, and isolation. Per-agent locks serialize within an agent. Cross-agent fully parallel.

```
Agent "explorer-1"  → ExecutionContainer A → IPython kernel A
Agent "coder-1"     → ExecutionContainer B → IPython kernel B
Agent "tester-1"    → ExecutionContainer C → IPython kernel C
```

Max 8 concurrent containers (configurable).

---

## What Gets Deleted

| File/Component | Reason |
|----------------|--------|
| `kernel_manager.py` | Replaced by `container_manager.py` using ipybox |
| `repl/bootstrap.py` | IPython handles execution natively |
| `repl/tool_stubs.py` | ipybox's `generate_mcp_sources()` handles this |
| `make_kernel_code()` in all 15 tool modules | Agents write code directly |
| 15 `@mcp.tool()` decorators in server.py | Replaced by single `ptc_execute` |
| `test_kernel_manager.py` | Old Docker subprocess tests |
| `test_server.py` | Old 15-tool tests |
| `test_tools.py` | Old `make_kernel_code()` tests |
| Sentinel constants | No sentinel protocol needed |
| Fallback config | No fallback to old approach |

---

## Implementation Order

### Phase 1: ipybox Integration
1. `pip install ipybox` + pull Docker image
2. Create `container_manager.py` — ExecutionContainer lifecycle, ExecutionClient management
3. Verify: create container, execute `print("hello")`, get result
4. Verify: state persists across calls (`x = 1` then `print(x)`)

### Phase 2: Tool Generation + Dispatch
1. Create `tool_dispatch.py` — route to existing `fallback()` functions
2. Integrate with ipybox's `ResourceClient.generate_mcp_sources()` or manual tool stub injection
3. Verify: `await read_file(path="README.md")` works end-to-end
4. Verify: role scoping (explorer can't call write_file)

### Phase 3: MCP Server
1. Rewrite `server.py` — single `ptc_execute` + `ptc_shutdown` + `ptc_status`
2. Clean tool_implementations — remove all `make_kernel_code()`
3. Update config.json
4. Verify: register as MCP server, call from Claude Code

### Phase 4: Tests
1. `test_container_manager.py` — lifecycle, state persistence, parallel containers
2. `test_tool_dispatch.py` — routing, permissions, error handling
3. `test_ptc_execute.py` — end-to-end with tool calls
4. Delete old test files

### Phase 5: Cleanup
1. Delete `kernel_manager.py`, `repl/` directory
2. Delete old test files
3. Update `requirements.txt`
4. Update agent instructions with `ptc_execute` usage examples

---

## Open Questions (To Resolve During Phase 1-2)

1. **ipybox tool approval gate API** — Exact mechanism for intercepting tool calls from container. Need to read ipybox source to understand callback/event API.
2. **Volume mounting** — How to mount `/workspace` read-only via ipybox's `ExecutionContainer` constructor. May need custom Docker flags.
3. **Network isolation** — ipybox containers may need network for port-based communication with host. Verify `--network none` compatibility or if ipybox uses a different isolation model.
4. **Resource limits** — How to set memory/CPU limits via ipybox API vs raw Docker flags.
5. **Container image customization** — Whether we need project-specific packages pre-installed or if the base ipybox image suffices.

These are implementation details, not architectural decisions. The architecture is settled.

---

## Verification

1. `pytest tests/ptc/ -v` — all tests pass
2. Container creates: `await manager.get_or_create("test", "explorer")` succeeds
3. Simple execution: `ptc_execute(code="print('hello')")` → `{"stdout": "hello\n"}`
4. Tool calling: `ptc_execute(code="data = await read_file(path='README.md')\nprint(len(data['content']))")` → file length
5. State persistence: two sequential calls, second references first's variable
6. Role scoping: explorer cannot `await write_file(...)`
7. Parallel agents: two agents execute simultaneously without interference
8. Token measurement: compare context sizes between old 15-tool approach and PTC
