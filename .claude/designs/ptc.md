# PTC Consolidated Plan: IPC-Based Programmatic Tool Calling

**Status:** Plan complete, not yet implemented
**Date:** 2026-02-28 (revised 2026-02-28: REPL pool, runtime pip, per-role PTC skill, sub-agent delegation)
**Supersedes:** `ptc-rewrite-plan.md` (ipybox approach), `quirky-hugging-nest.md` (raw IPC plan)

---

## Context

The current PTC implementation has 15 hardcoded MCP tools — structured API wrappers, not true programmatic tool calling. Agents call fixed-signature tools, get raw results dumped into their context window, and never write code. This is the opposite of PTC.

**True PTC** (as Anthropic implements it): agents write Python code containing `await tool_name(...)` calls. Code runs in a persistent sandbox. At each tool call, execution **pauses**, the host fulfills the call, and execution **resumes**. Tool results never enter the model's context — only the final `print()` output does.

**This plan** replaces the 15-tool approach with a single `ptc_execute` MCP tool backed by:
- Persistent Docker containers with base image + role-specific runtime pip install
- REPL pool per container (multiple agents/sub-agents share one container, separate namespaces)
- Unix domain socket IPC for pause-resume tool calling
- NDJSON wire protocol (human-readable, debuggable)
- Host-side tool dispatch to existing `fallback()` functions
- `ptc_tools` library importable inside containers
- Per-role PTC skill specs embedded in a standalone `ptc-sandbox` skill
- Sub-agent PTC delegation patterns in `sub-agent-delegation` skill
- Comprehensive JSONL observability
- Docker is required — no degraded fallback mode

---

## Architecture

```
Agent (Claude Code teammate)
  │
  ├── MCP call ──→ ptc_execute(agent_id, role, code)
  │                     │
  │                     ├── PTC MCP Server (host process)
  │                     │     │
  │                     │     ├── IPC Host (Unix socket per container)
  │                     │     │     ├── Send code to REPL via socket
  │                     │     │     ├── Receive tool_call requests
  │                     │     │     ├── Dispatch to host tools
  │                     │     │     ├── Send tool_result back
  │                     │     │     └── Log all events to JSONL
  │                     │     │
  │                     │     ├── Tool Dispatcher
  │                     │     │     ├── ptc_tools functions (host-local)
  │                     │     │     └── subprocess (pytest, git, ruff)
  │                     │     │
  │                     │     └── Container Manager (persistent, eager-start)
  │                     │           ├── ptc-explorer (Docker container)
  │                     │           │     ├── REPL-0: parent explorer
  │                     │           │     ├── REPL-1: sub-agent (on demand)
  │                     │           │     └── REPL-2: sub-agent (on demand)
  │                     │           ├── ptc-coder-1 (Docker container)
  │                     │           │     └── REPL-0: coder agent
  │                     │           ├── ptc-coder-2 (Docker container)
  │                     │           │     └── REPL-0: coder agent
  │                     │           └── ... (5-7 containers, up to 8 max)
  │                     │
  │                     └── Only final print() output returns to agent context
  │
  └── Structured summary ←── (37-93% token reduction)
```

### REPL Pool: Multiple Agents Per Container

Each Docker container can host multiple REPL processes (Python interpreters). All REPLs
share the container's installed packages (read-only filesystem) but have independent
namespaces (separate Python process memory).

```
Container "ptc-explorer" (1 Docker container, packages installed once)
  ├── /usr/local/lib/python3.11/site-packages/  ← shared across all REPLs
  ├── /workspace/  ← project mount, shared read-only
  ├── /ptc_ipc/    ← IPC socket directory
  │
  ├── REPL-0 (parent explorer agent)
  │     ├── PID 42, started at container creation
  │     ├── namespace: {files: {...}, graph: nx.DiGraph(), ...}
  │     └── IPC: /ptc_ipc/repl-0.sock
  │
  ├── REPL-1 (sub-agent "structure analysis")
  │     ├── PID 87, started on demand (~100ms)
  │     ├── namespace: {} (fresh)
  │     └── IPC: /ptc_ipc/repl-1.sock
  │
  └── REPL-2 (sub-agent "coverage analysis")
        ├── PID 91, started on demand (~100ms)
        ├── namespace: {} (fresh)
        └── IPC: /ptc_ipc/repl-2.sock
```

Starting a new REPL = `docker exec -d container python3 /ptc_runtime/runtime.py --socket repl-N.sock`
Cost: ~100ms. No pip install (packages on shared filesystem). No Docker creation.
Sub-agent terminates → REPL killed. No cleanup cost.

### IPC: Unix Domain Sockets via Bind Mount

```
Host                                   Docker Container (--network none)
  │                                         │
  │  /tmp/ptc-ipc/{agent-id}/               │  /ptc_ipc/
  │    └── ptc.sock  ◄──────────────────────│    └── ptc.sock
  │         ▲                               │         │
  │    asyncio server                       │    asyncio client
  │    (per container)                      │    (sandbox runtime)
  │                                         │
  │  Bidirectional NDJSON:                  │
  │    ← tool_call requests                 │
  │    → tool_result responses              │
  │    → execute commands                   │
  │    ← execution complete/error           │
```

Unix sockets work with `--network none` because they use the filesystem, not the network stack.

---

## IPC Protocol (NDJSON over Unix Socket)

### Message Types

**Host → Container:**
```json
{"type": "execute", "exec_id": "uuid", "code": "agent_python_code_here"}
{"type": "tool_result", "call_id": "uuid", "content": "{...}", "error": null}
{"type": "cancel", "exec_id": "uuid", "reason": "timeout"}
{"type": "ping"}
```

**Container → Host:**
```json
{"type": "tool_call", "call_id": "uuid", "tool": "run_tests", "input": {"test_path": "tests/"}}
{"type": "complete", "exec_id": "uuid", "stdout": "...", "stderr": "...", "return_code": 0, "namespace_keys": ["data", "results"]}
{"type": "error", "exec_id": "uuid", "message": "...", "traceback": "..."}
{"type": "pong"}
```

### Flow: Single ptc_execute Call

```
1. MCP server receives ptc_execute(agent_id, role, code)
2. Server sends: {"type": "execute", "exec_id": "e1", "code": "..."}
3. Container runtime exec()s the code
4. Code hits: result = await run_tests("tests/")
5. Container sends: {"type": "tool_call", "call_id": "c1", "tool": "run_tests", "input": {...}}
6. Host dispatcher runs pytest via subprocess, gets result
7. Host sends: {"type": "tool_result", "call_id": "c1", "content": "{...}"}
8. Container runtime resolves the future, code continues
9. Code hits: print(json.dumps(summary))
10. Container sends: {"type": "complete", "exec_id": "e1", "stdout": "...", "return_code": 0}
11. MCP server returns stdout to agent
```

### Parallel Tool Calls (asyncio.gather)

```python
# Agent code:
results = await asyncio.gather(
    run_tests("tests/unit/"),
    run_linter("src/"),
)

# Container sends both:
#   {"type": "tool_call", "call_id": "c1", "tool": "run_tests", ...}
#   {"type": "tool_call", "call_id": "c2", "tool": "run_linter", ...}
# Host dispatches both concurrently, responds to each.
# Both futures resolve, code continues.
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| IPC mechanism | Unix domain sockets (bind mount) | Works with `--network none`, bidirectional, async-native, handles parallel calls |
| Container lifecycle | Persistent (long-lived) | State across calls, amortize startup, match Anthropic model |
| Container startup | **Eager** (on SubagentStart, not first ptc_execute) | Eliminates cold-start latency on first call |
| MCP tools | **Single `ptc_execute`** (no dual-path) | One execution path, one code path to test, agents batch or not at their discretion |
| Docker requirement | **Required, no fallback** | Without Docker there's no isolation. Agents use native Read/Bash if Docker unavailable |
| Tool dispatch | ptc_tools → host subprocess | Covers all tool types via existing fallback() functions |
| Dispatch backpressure | `asyncio.Semaphore(4)` | Prevents host saturation from parallel tool calls |
| Wire format | NDJSON | Simple, streamable, debuggable, human-readable |
| Namespace persistence | Python `dict` in `exec()`, size monitoring | Variables survive across calls; warn at 256MB, evict at 512MB |
| Crash recovery | **Always return error, never re-execute** | Re-execution is dangerous if code had side effects |
| Output cap | 64KB default, configurable | Prevents context flooding |
| Socket permissions | `0o666` | Container runs as non-root, needs access |
| Image strategy | **Base image + runtime pip install** | One image to build/maintain. Role-specific packages installed at container creation (~15s). Upgrade to multi-image when package lists stabilize. |
| REPL pool | **Multiple REPLs per container** | Sub-agents share parent's container (packages installed once). Each REPL = separate Python process (~100ms start). Independent namespaces. Avoids N containers for N sub-agents. |
| Sub-agent containers | **Sub-agents use parent's container** | kernel_manager routes sub-agent ptc_execute to a new REPL in the parent's container. No separate container creation, no pip install overhead. |
| Handoff containers | **Persist container, reset REPL** | When agent hands off, replacement agent inherits container (packages intact). Old REPL killed, fresh REPL started (clean namespace). Saves 15s pip install per handoff. |
| PTC replaces sub-agents | **PTC reduces sub-agent count** | Explorer no longer needs 5 Haiku sub-agents to read files — one ptc_execute reads 50 files. Sub-agents survive only for tasks needing LLM reasoning at intermediate steps. |
| PTC skill architecture | **Standalone skill with per-role specs** | `ptc-sandbox` SKILL.md covers universal mechanics (~900 tokens). Per-role PTC specs (recipes) live in each agent's primary skill. `sub-agent-delegation` teaches PTC delegation patterns. |

---

## File Structure

```
~/.claude/mcp/ptc-server/
  server.py                        # REWRITTEN: single ptc_execute + ptc_status
  container_manager.py             # NEW: REPL pool, parent-child routing, runtime pip, handoff
  ipc_protocol.py                  # NEW: message types, NDJSON encode/decode
  ipc_host.py                      # NEW: host-side socket server per REPL
  tool_dispatcher.py               # NEW: route tool calls to strategies
  tool_registry.py                 # MODIFIED: add execute perm, tool_strategies
  event_logger.py                  # NEW: JSONL observability (container + REPL pool events)
  config.json                      # REWRITTEN: single ptc_execute, strategies, IPC, repl_pool
  requirements.txt                 # UPDATED: add any MCP deps
  Dockerfile                       # NEW: thin base image (python:3.11-slim + git + minimal)
  build-image.sh                   # NEW: build/verify base image
  sandbox_runtime/
    __init__.py                    # NEW
    runtime.py                     # NEW: container REPL process (accepts --socket arg)
    executor.py                    # NEW: code execution + namespace management
  ptc_tools/
    __init__.py                    # NEW: convenience re-exports
    _base.py                       # NEW: get_project_root(), constants
    files.py                       # NEW: read_file(), write_file(), list_dir()
    search.py                      # NEW: search_code(), analyze_imports(), analyze_structure()
    testing.py                     # NEW: run_tests(), run_linter(), coverage_summary()
    git.py                         # NEW: git_diff_summary(), diff_summary(), list_changes()
    tokens.py                      # NEW: count_tokens()
    network.py                     # NEW: web_fetch_summary(), mcp_call_summary() (host-only)
  ipc_sandbox.py                   # NEW: container-side socket client
  tool_implementations/            # KEPT: fallback() functions (remove make_kernel_code)
    (all 15 existing modules)

~/personal/agentic_workflow/skills/ptc-sandbox/
  SKILL.md                         # NEW: universal PTC mechanics (~900 tokens)
  role-specs/
    explorer.md                    # NEW: explorer packages + PTC recipes
    coder.md                       # NEW: coder packages + PTC recipes
    auditor.md                     # NEW: auditor packages + PTC recipes
    tester.md                      # NEW: tester packages + PTC recipes
    researcher.md                  # NEW: researcher packages + PTC recipes
    strategist.md                  # NEW: strategist packages + PTC recipes
  references/
    tool-calling-patterns.md       # NEW: await, asyncio.gather, error handling
    anti-patterns.md               # NEW: what NOT to do
  specializations/
    robotics-cv.md                 # NEW: domain packages + patterns

~/personal/agentic_workflow/tests/ptc/
  test_ipc_protocol.py             # NEW: message encoding/decoding
  test_ipc_host.py                 # NEW: socket server, tool call handling
  test_sandbox_runtime.py          # NEW: runtime lifecycle, code execution
  test_tool_dispatcher.py          # NEW: dispatch routing, strategies
  test_container_manager.py        # NEW: REPL pool, parent-child routing, handoff
  test_repl_pool.py                # NEW: multi-REPL isolation, concurrent execution
  test_ptc_tools.py                # NEW: all ptc_tools functions
  test_ptc_execute.py              # NEW: end-to-end MCP tool tests
  test_event_logger.py             # NEW: observability event tests (incl REPL events)
  conftest.py                      # REWRITTEN: new fixtures
```

---

## Component 1: IPC Protocol (`ipc_protocol.py`)

Message types as dataclasses, NDJSON serialization, call ID generation.

```python
"""IPC protocol: NDJSON message types for host ↔ container communication."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

# --- Host → Container ---

@dataclass
class ExecuteMsg:
    exec_id: str
    code: str
    type: str = "execute"

@dataclass
class ToolResultMsg:
    call_id: str
    content: str  # JSON-encoded result
    error: str | None = None
    type: str = "tool_result"

@dataclass
class CancelMsg:
    exec_id: str
    reason: str = "timeout"
    type: str = "cancel"

@dataclass
class PingMsg:
    type: str = "ping"

# --- Container → Host ---

@dataclass
class ToolCallMsg:
    call_id: str
    tool: str
    input: dict[str, Any]
    type: str = "tool_call"

@dataclass
class CompleteMsg:
    exec_id: str
    stdout: str
    stderr: str = ""
    return_code: int = 0
    namespace_keys: list[str] = field(default_factory=list)
    namespace_size_kb: int = 0
    type: str = "complete"

@dataclass
class ErrorMsg:
    exec_id: str
    message: str
    traceback: str = ""
    type: str = "error"

@dataclass
class PongMsg:
    type: str = "pong"


def encode(msg) -> bytes:
    """Serialize message to NDJSON line."""
    return (json.dumps(asdict(msg), separators=(",", ":")) + "\n").encode()

def decode(line: bytes) -> dict:
    """Deserialize NDJSON line to dict."""
    return json.loads(line.strip())

def new_call_id() -> str:
    return f"c-{uuid.uuid4().hex[:12]}"

def new_exec_id() -> str:
    return f"e-{uuid.uuid4().hex[:12]}"

MAX_MESSAGE_SIZE = 1_048_576  # 1MB per message
```

---

## Component 2: IPC Host (`ipc_host.py`)

Host-side Unix socket server. One instance per container. Handles execute → tool_call → tool_result → complete lifecycle.

```python
"""Host-side IPC: Unix socket server managing one container connection."""

class IpcHost:
    def __init__(self, socket_path: str, tool_dispatcher, event_logger):
        self._socket_path = socket_path
        self._dispatcher = tool_dispatcher
        self._logger = event_logger
        self._reader = None
        self._writer = None
        self._pending_futures: dict[str, asyncio.Future] = {}  # exec_id → future
        self._server = None

    async def start(self):
        """Create socket, start listening."""

    async def send_execute(self, exec_id: str, code: str, role: str, timeout: int) -> CompleteMsg | ErrorMsg:
        """Send code, handle tool calls, return final result."""
        # 1. Send ExecuteMsg
        # 2. Read responses in loop:
        #    - ToolCallMsg → dispatch via self._dispatcher, send ToolResultMsg back
        #    - CompleteMsg → return
        #    - ErrorMsg → return
        # 3. On timeout → send CancelMsg
        # 4. Log every event

    async def health_check(self) -> bool:
        """Send ping, wait for pong."""

    async def stop(self):
        """Close connection and remove socket file."""
```

Key: `send_execute` handles the full pause-resume loop. Multiple `ToolCallMsg` responses can arrive (parallel tool calls from `asyncio.gather`). Each is dispatched concurrently with semaphore backpressure.

---

## Component 3: Sandbox Runtime (`sandbox_runtime/`)

Runs inside the container. Entry point: `python3 /ptc_runtime/runtime.py`.

### `runtime.py`

```python
"""Container entry point. Connects to host IPC, executes code blocks."""

async def main():
    # 1. Connect to Unix socket at /ptc_ipc/ptc.sock
    # 2. Register tool stubs in namespace (from manifest injected at startup)
    # 3. Loop:
    #    a. Read ExecuteMsg from socket
    #    b. Execute code via executor.py
    #    c. Send CompleteMsg or ErrorMsg
    # 4. Handle CancelMsg (interrupt running code)
    # 5. Handle PingMsg (respond with PongMsg)
```

### `executor.py`

```python
"""Code execution engine with persistent namespace."""

class Executor:
    def __init__(self, ipc_client, allowed_tools: list[str]):
        self._namespace: dict = {}  # Persistent across calls
        self._ipc_client = ipc_client
        self._setup_tool_stubs(allowed_tools)

    def _setup_tool_stubs(self, tools: list[str]):
        """Generate async tool functions in namespace."""
        for tool_name in tools:
            async def _stub(_name=tool_name, **kwargs):
                return await self._ipc_client.call_tool(_name, kwargs)
            self._namespace[tool_name] = _stub

    async def execute(self, code: str) -> tuple[str, str, int, list[str]]:
        """Execute code in persistent namespace. Returns (stdout, stderr, return_code, namespace_keys)."""
        # 1. Redirect stdout/stderr to StringIO
        # 2. Compile code (detect syntax errors early)
        # 3. If code contains 'await': wrap in async def + asyncio.run()
        # 4. exec() in self._namespace
        # 5. Extract new locals back to namespace
        # 6. Return captured stdout, stderr, return code
        # 7. Monitor namespace size, warn/evict if needed
```

### `ipc_sandbox.py` (container-side socket client)

```python
"""Container-side IPC: connects to host socket, provides tool calling interface."""

class IpcClient:
    async def connect(self, socket_path: str):
        """Connect to host Unix socket."""

    async def call_tool(self, tool_name: str, params: dict) -> dict:
        """Send tool_call, await tool_result. Used by executor tool stubs."""
        call_id = new_call_id()
        msg = ToolCallMsg(call_id=call_id, tool=tool_name, input=params)
        await self._send(msg)
        # Wait for matching tool_result
        result = await self._wait_for_result(call_id)
        if result.error:
            raise ToolError(result.error)
        return json.loads(result.content)

    async def _response_handler(self):
        """Route incoming tool_results to pending futures."""
```

---

## Component 4: ptc_tools Library (`ptc_tools/`)

Refactored from `tool_implementations/`. Clean Python functions usable on both host and inside container.

```
ptc_tools/
  __init__.py    # Convenience re-exports
  _base.py       # get_project_root() (detects /workspace vs host), constants
  files.py       # read_file(), write_file(), list_dir()
  search.py      # search_code(), analyze_imports(), analyze_structure()
  testing.py     # run_tests(), run_linter(), coverage_summary()
  git.py         # git_diff_summary(), diff_summary(), list_changes()
  tokens.py      # count_tokens()
  network.py     # web_fetch_summary(), mcp_call_summary() (host-only)
```

Each function signature: `(params) -> dict`. No `make_kernel_code()`. No separate `fallback()`. One function per tool.

Mounted into container as `/ptc_tools_lib:ro`. Also used by host-side dispatcher.

**Migration path**: Extract logic from existing `tool_implementations/*.py` `fallback()` functions. Remove `make_kernel_code()` from all 15 modules. Old `tool_implementations/` directory kept for reference during migration, deleted after.

---

## Component 5: Tool Dispatcher (`tool_dispatcher.py`)

Routes tool calls from IPC to the appropriate execution strategy.

```python
"""Host-side tool dispatcher. Routes tool calls to execution strategies."""

class ToolDispatcher:
    def __init__(self, registry, project_root: str, event_logger, max_concurrent: int = 4):
        self._registry = registry
        self._project_root = project_root
        self._logger = event_logger
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def dispatch(self, role: str, tool_name: str, input: dict, exec_id: str) -> str:
        """Execute tool on host. Returns JSON result string."""
        if not self._registry.is_allowed(role, tool_name):
            return json.dumps({"error": f"Tool '{tool_name}' not permitted for role '{role}'"})

        async with self._semaphore:
            self._logger.log_tool_call_start(exec_id, tool_name, input)
            start = time.monotonic()

            strategy = self._registry.get_strategy(tool_name)
            if strategy == "ptc_tools":
                result = ptc_tools_dispatch(tool_name, input, self._project_root)
            elif strategy == "subprocess":
                result = await subprocess_dispatch(tool_name, input, self._project_root)
            elif strategy == "host_fs":
                result = host_fs_dispatch(tool_name, input, self._project_root)
            else:
                result = {"error": f"Unknown strategy '{strategy}' for tool '{tool_name}'"}

            duration_ms = int((time.monotonic() - start) * 1000)
            self._logger.log_tool_call_complete(exec_id, tool_name, duration_ms, result)
            return json.dumps(result)
```

Strategy mapping:
```
ptc_tools:   read_file, list_dir, search_code, analyze_imports, analyze_structure, count_tokens
subprocess:  run_tests, run_linter, coverage_summary, git_diff_summary, diff_summary, list_changes
host_fs:     write_file
host_network: web_fetch_summary, mcp_call_summary
```

---

## Component 6: Container Manager (`container_manager.py`)

Manages persistent Docker containers with REPL pool. Each container can host multiple
REPL processes — one per agent (parent or sub-agent) using that container. All REPLs
share the container's installed packages and filesystem, but have independent namespaces.

```python
"""Persistent container lifecycle management with REPL pool and IPC."""

@dataclass
class ReplHandle:
    """One Python REPL process inside a container."""
    agent_id: str
    repl_index: int
    ipc: IpcHost           # IPC connection to this specific REPL
    started_at: datetime
    exec_count: int = 0
    tool_call_count: int = 0

@dataclass
class ContainerInfo:
    """One Docker container, potentially hosting multiple REPLs."""
    container_name: str
    role: str
    created_at: datetime
    last_used_at: datetime
    repls: dict[str, ReplHandle]  # agent_id → REPL
    max_repls: int = 6            # safety limit per container
    packages_installed: bool = False

class ContainerManager:
    def __init__(self, config: dict, project_root: str, registry, event_logger):
        self._config = config
        self._project_root = project_root
        self._registry = registry
        self._logger = event_logger
        self._containers: dict[str, ContainerInfo] = {}       # container_name → info
        self._agent_to_container: dict[str, str] = {}         # agent_id → container_name
        self._parent_map: dict[str, str] = {}                 # sub-agent_id → parent_agent_id
        self._locks: dict[str, asyncio.Lock] = {}             # per-agent execution lock

    async def get_or_create_repl(self, agent_id: str, role: str) -> ReplHandle:
        """Route an agent to the right container and REPL.

        Routing priority:
        1. Agent already has a REPL → return it
        2. Agent is a sub-agent → start new REPL in parent's container
        3. Idle container with same role exists → reuse it (new REPL)
        4. Under max_containers limit → create new container + REPL
        """
        # 1. Existing REPL?
        if agent_id in self._agent_to_container:
            container = self._containers[self._agent_to_container[agent_id]]
            return container.repls[agent_id]

        # 2. Sub-agent? Use parent's container.
        parent_id = self._parent_map.get(agent_id)
        if parent_id and parent_id in self._agent_to_container:
            container_name = self._agent_to_container[parent_id]
            container = self._containers[container_name]
            if len(container.repls) < container.max_repls:
                repl = await self._start_repl_in_container(container, agent_id)
                return repl

        # 3. Idle same-role container?
        for name, container in self._containers.items():
            if container.role == role and len(container.repls) == 0:
                repl = await self._start_repl_in_container(container, agent_id)
                return repl

        # 4. Create new container
        container = await self._create_container(role)
        repl = await self._start_repl_in_container(container, agent_id)
        return repl

    async def _create_container(self, role: str) -> ContainerInfo:
        """Create Docker container with base image, install role packages."""
        # 1. docker run -d with mounts, limits (NO entrypoint — container stays alive)
        # 2. pip install role-specific packages (async, ~15s)
        # 3. Log container_create event
        # 4. Mark packages_installed = True
        # See ROLE_PACKAGES below for per-role package lists

    async def _start_repl_in_container(self, container: ContainerInfo, agent_id: str) -> ReplHandle:
        """Start a new REPL process inside an existing container. ~100ms."""
        # 1. repl_index = len(container.repls)
        # 2. socket_name = f"repl-{repl_index}.sock"
        # 3. docker exec -d container python3 /ptc_runtime/runtime.py --socket socket_name
        # 4. Create IpcHost for this REPL's socket
        # 5. Wait for pong (~100ms)
        # 6. Register in container.repls and self._agent_to_container
        # 7. Log repl_start event

    async def register_parent(self, sub_agent_id: str, parent_agent_id: str):
        """Register a sub-agent's parent. Called when Task tool spawns sub-agents."""
        self._parent_map[sub_agent_id] = parent_agent_id

    async def execute_code(self, agent_id: str, code: str, timeout: int) -> dict:
        """Execute code in agent's REPL via IPC."""
        # 1. Acquire per-agent lock (serializes within one REPL)
        # 2. repl.ipc.send_execute(code)
        # 3. Return result dict

    async def handle_handoff(self, old_agent_id: str, new_agent_id: str, reset_namespace: bool = True):
        """Transfer container from old agent to new agent on handoff.

        Container persists (packages intact). Old REPL killed, fresh REPL started.
        Saves ~15s pip install that would be needed for a new container.
        """
        # 1. Find old agent's container
        # 2. Kill old agent's REPL process
        # 3. Remove old agent from container.repls
        # 4. Start fresh REPL for new agent (clean namespace)
        # 5. Update _agent_to_container mapping
        # 6. Log handoff event

    async def stop_repl(self, agent_id: str):
        """Stop a specific REPL (sub-agent finished). Container stays alive."""

    async def stop_container(self, container_name: str):
        """Stop container and all its REPLs."""

    async def stop_all(self):
        """Stop all containers."""

    async def health_check(self, agent_id: str) -> dict:
        """Ping agent's REPL, return container docker stats."""

    async def reset_namespace(self, agent_id: str):
        """Clear persistent state in agent's REPL (not the whole container)."""
```

### Role-Specific Package Installation

```python
"""Per-role packages installed via pip at container creation time.
Base image has system tools + stdlib only. Role packages add ~8-35 MB."""

ROLE_PACKAGES = {
    "explorer": [
        # Code analysis
        "tree-sitter", "tree-sitter-python", "tree-sitter-typescript",
        "ast-grep-py", "jedi", "pyan3",
        # Metrics
        "radon", "vulture", "cognitive-complexity", "lizard", "cohesion",
        # Graphs & data
        "networkx", "pandas",
        # Git analysis
        "gitpython", "pydriller",
        # Token budgets
        "tiktoken",
    ],
    "coder": [
        # Profiling
        "pyinstrument", "pympler", "line-profiler",
        # Code quality
        "perflint", "radon", "cognitive-complexity",
        # Test analysis
        "coverage", "pytest-cov", "diff-cover",
        # Security
        "bandit",
        # Diff parsing
        "unidiff",
        # Token budgets
        "tiktoken",
    ],
    "auditor": [
        # Same as coder (audits coder output) plus extras
        "radon", "cognitive-complexity", "vulture", "cohesion",
        "coverage", "pytest-cov", "diff-cover",
        "bandit", "unidiff",
        "networkx", "pydriller", "wily",
        "tiktoken",
    ],
    "tester": [
        # Property-based testing
        "hypothesis",
        # Coverage analysis
        "coverage", "pytest-cov",
        # Performance verification
        "pyinstrument", "big-o",
        # Memory testing
        "memray",
        # Metrics
        "radon", "cognitive-complexity",
        "tiktoken",
    ],
    "researcher": [
        # Content extraction
        "trafilatura", "html2text", "markdownify", "readability-lxml",
        # Document parsing
        "pypdf", "pdfplumber", "markdown-it-py",
        # Deduplication
        "rapidfuzz",
        # Data processing
        "pandas",
        "tiktoken",
    ],
    "strategist": [
        # Validation only — lightest set
        "networkx",
        "radon", "cognitive-complexity",
        "tiktoken",
    ],
}
```

Changing what an agent gets = editing this dict. No Dockerfile rebuild. Package lists
stabilize → freeze into per-role Dockerfiles for faster startup (future optimization).

### Docker Container Configuration

```python
# Step 1: Create container from base image (no entrypoint — container idles)
cmd = [
    "docker", "run", "-d",
    "--name", container_name,
    "--memory", f"{limits['memory_mb']}m",
    "--cpus", str(limits["cpu_cores"]),
    "--network", "none",
    "--tmpfs", f"/tmp:size={limits['disk_mb']}m",
    # Mounts
    "-v", f"{project_root}:/workspace:ro",
    "-v", f"{socket_dir}:/ptc_ipc",
    "-v", f"{runtime_dir}:/ptc_runtime:ro",
    "-v", f"{ptc_tools_dir}:/ptc_tools_lib:ro",
    # Environment
    "-e", "PYTHONPATH=/workspace:/ptc_tools_lib",
    # Keep container alive (sleep infinity, REPLs started via docker exec)
    image,
    "sleep", "infinity",
]

# Step 2: Install role-specific packages (async, runs during agent startup)
packages = ROLE_PACKAGES.get(role, [])
if packages:
    await _docker_exec(container_name, f"pip install --no-cache-dir {' '.join(packages)}")

# Step 3: Start first REPL process
await _docker_exec_detached(container_name,
    "python3 /ptc_runtime/runtime.py --socket repl-0.sock")
```

### Container Lifecycle

```
Workflow Session Container Flow:
═══════════════════════════════════════════════════════════════════

PHASE: Planning
  Strategist spawns → CREATE container "ptc-strategist"
                     → pip install networkx, radon, cognitive-complexity, tiktoken (~5s)
                     → start REPL-0
  Strategist uses PTC for plan validation
  Strategist finishes → REPL-0 killed, container IDLE

PHASE: Implementation
  Explorer spawns   → REUSE idle "ptc-strategist"? No, different packages.
                    → CREATE container "ptc-explorer"
                    → pip install jedi, tree-sitter, networkx, ... (~15s)
                    → start REPL-0
  Explorer delegates 3 sub-agents for multi-part query
                    → start REPL-1, REPL-2, REPL-3 in same container (~100ms each)
  Sub-agents finish → kill REPL-1, REPL-2, REPL-3
  Explorer finishes → REPL-0 stays (explorer may be needed again)

  Coder-1 spawns    → CREATE container "ptc-coder-1"
                    → pip install pyinstrument, coverage, bandit, ... (~10s)
                    → start REPL-0
  Coder-2 spawns    → CREATE container "ptc-coder-2"
                    → pip install (same packages) (~10s)
                    → start REPL-0

  Coder-1 hits context pressure → HANDOFF
                    → kill REPL-0, start fresh REPL-0 for Coder-1-replacement
                    → Container persists (no pip install needed!)
                    → Namespace reset (fresh start for new coder)

  Tester spawns     → CREATE container "ptc-tester"
                    → pip install hypothesis, coverage, memray, ... (~10s)

  Auditor spawns    → Auditor and coder packages overlap heavily
                    → REUSE "ptc-coder-1" (coder finished, container idle)
                    → pip install missing packages only (wily, cohesion) (~3s)
                    → start REPL-0

PHASE: Hardening
  Auditor hardening → SAME container, runtime install semgrep (~30s, one-time)

CLEANUP:
  All agents done   → idle_timeout (300s) → containers destroyed
                    → Or explicit ptc_shutdown_all()

Container count at peak: 5-6 (explorer, coder-1, coder-2, tester, auditor)
REPL count at peak: 8-10 (including sub-agent REPLs)
```

---

## Component 7: MCP Server (`server.py`)

Single `ptc_execute` tool. No dual-path.

```python
"""PTC MCP Server — single ptc_execute tool with observability."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ptc-sandbox")

@mcp.tool()
async def ptc_execute(agent_id: str, role: str, code: str, timeout: int = 0) -> str:
    """Execute Python code in a persistent sandboxed container.

    The container has:
    - Persistent namespace — variables from previous calls survive
    - Tool functions callable via await: data = await read_file(path="src/main.py")
    - Parallel tools: results = await asyncio.gather(run_tests("tests/"), run_linter("src/"))
    - Rich Python packages: pandas, numpy, networkx, ast, etc.
    - ptc_tools library: from ptc_tools.search import search_code
    - /workspace mount with project files (read-only)

    Only print() output returns to your context. All intermediate data stays in container.

    Args:
        agent_id: Unique agent identifier.
        role: Agent role (explorer, coder, tester, auditor, strategist, researcher).
        code: Python code to execute. Top-level await is supported.
        timeout: Optional timeout in seconds (0 = use default from config).
    """
    if not container_manager.docker_available:
        return json.dumps({"error": "Docker required for PTC sandbox. Run: docker info"})

    if not registry.is_allowed(role, "execute"):
        return json.dumps({"error": f"execute not permitted for role {role}"})

    effective_timeout = timeout if timeout > 0 else config["resource_limits"]["timeout_seconds"]

    handle = await container_manager.get_or_create(agent_id, role)
    result = await container_manager.execute_code(agent_id, code, effective_timeout)

    return _cap_output(json.dumps(result), config["resource_limits"]["max_output_bytes"])


@mcp.tool()
async def ptc_status(agent_id: str = "") -> str:
    """Get PTC container status. If agent_id is empty, returns all containers."""
    if agent_id:
        info = await container_manager.health_check(agent_id)
        return json.dumps(info)
    return json.dumps(container_manager.get_all_status())


@mcp.tool()
async def ptc_reset_namespace(agent_id: str) -> str:
    """Clear persistent namespace in agent's container."""
    await container_manager.reset_namespace(agent_id)
    return json.dumps({"status": "namespace_reset", "agent_id": agent_id})


@mcp.tool()
async def ptc_shutdown(agent_id: str) -> str:
    """Shut down container for a specific agent."""
    await container_manager.stop(agent_id)
    return json.dumps({"status": "stopped", "agent_id": agent_id})


@mcp.tool()
async def ptc_shutdown_all() -> str:
    """Shut down all PTC containers."""
    await container_manager.stop_all()
    return json.dumps({"status": "all_stopped"})
```

---

## Component 8: Observability (`event_logger.py`)

All PTC events logged to `~/.claude/logs/ptc-events.jsonl`. Structured for grep/jq, with correlation IDs.

### Event Envelope

```json
{
  "ts": "2026-02-28T10:05:00.123Z",
  "event": "tool_call_complete",
  "agent_id": "explorer-1",
  "exec_id": "e-abc123def456",
  "container": "ptc-explorer-1",
  "data": { ... }
}
```

### Event Types

**Container lifecycle:**
```
container_create   — data: {role, image, memory_mb}
container_ready    — data: {startup_ms, pip_install_ms, packages_installed}
container_stop     — data: {reason, uptime_s, total_executions, total_tool_calls}
container_crash    — data: {reason, memory_at_crash_mb, active_repls}
container_health   — data: {ping_latency_ms, memory_mb, cpu_pct, repl_count}
container_handoff  — data: {old_agent_id, new_agent_id, namespace_reset}
```

**REPL pool:**
```
repl_start         — data: {repl_index, parent_agent_id, is_sub_agent}
repl_stop          — data: {repl_index, reason, exec_count, uptime_s}
repl_crash         — data: {repl_index, reason, other_repls_affected: false}
pip_install_start  — data: {role, packages}
pip_install_done   — data: {role, duration_ms, packages_count}
pip_install_error  — data: {role, package, error}
```

**Code execution:**
```
exec_start         — data: {code_length, has_await, estimated_tools}
exec_complete      — data: {duration_ms, stdout_bytes, tool_calls, namespace_keys, namespace_size_kb}
exec_error         — data: {error, traceback_preview, duration_ms}
exec_timeout       — data: {timeout_seconds, tool_calls_before_timeout}
```

**Tool dispatch:**
```
tool_call_start    — data: {call_id, tool, params}
tool_call_complete — data: {call_id, tool, duration_ms, result_bytes, result_preview}
tool_call_error    — data: {call_id, tool, error}
tool_call_denied   — data: {call_id, tool, role, reason}
```

**IPC health:**
```
ipc_connect        — data: {socket_path}
ipc_disconnect     — data: {reason, pending_calls}
ipc_ping           — data: {latency_ms}
```

### Logging Design Decisions

- **`result_preview` (200 chars)** — not full result. Full results can be huge. Reproduce the call for full output.
- **`code_length` by default** — not full code. Add `"debug_log_code": true` in config to enable full code logging.
- **`namespace_size_kb`** — tracks namespace growth. Unbounded growth shows up before OOM.
- **`exec_id` correlation** — ties all events within one `ptc_execute` call together.
- **Append-only JSONL** — no rotation by default. Use `logrotate` or manual truncation for long-running systems.

### Query Examples

```bash
# All events for one execution
jq 'select(.exec_id=="e-def456")' ~/.claude/logs/ptc-events.jsonl

# All tool calls for an agent
jq 'select(.agent_id=="coder-1" and (.event | startswith("tool_call")))' ~/.claude/logs/ptc-events.jsonl

# Slow tool calls (>5s)
jq 'select(.event=="tool_call_complete" and .data.duration_ms > 5000)' ~/.claude/logs/ptc-events.jsonl

# Container crashes
jq 'select(.event=="container_crash")' ~/.claude/logs/ptc-events.jsonl

# Denied tool calls (permission violations)
jq 'select(.event=="tool_call_denied")' ~/.claude/logs/ptc-events.jsonl

# Namespace growth over time for an agent
jq 'select(.agent_id=="explorer-1" and .event=="exec_complete") | {ts, kb: .data.namespace_size_kb}' ~/.claude/logs/ptc-events.jsonl
```

### Implementation

```python
"""PTC event logger — structured JSONL for all sandbox events."""

import json
import time
from pathlib import Path

LOG_FILE = Path.home() / ".claude" / "logs" / "ptc-events.jsonl"

class PtcEventLogger:
    def __init__(self, log_path: Path = LOG_FILE):
        self._log_path = log_path
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, event: str, agent_id: str = "", exec_id: str = "",
              container: str = "", data: dict | None = None):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            "agent_id": agent_id,
            "exec_id": exec_id,
            "container": container,
            "data": data or {},
        }
        # Remove empty string fields for compactness
        entry = {k: v for k, v in entry.items() if v}
        with open(self._log_path, "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    # Convenience methods for each event type
    def log_container_create(self, agent_id, container, role, image, memory_mb): ...
    def log_container_ready(self, agent_id, container, startup_ms): ...
    def log_exec_start(self, agent_id, exec_id, code_length, has_await): ...
    def log_exec_complete(self, agent_id, exec_id, duration_ms, stdout_bytes, tool_calls, namespace_keys, namespace_size_kb): ...
    def log_tool_call_start(self, exec_id, tool, params): ...
    def log_tool_call_complete(self, exec_id, tool, duration_ms, result): ...
    def log_tool_call_error(self, exec_id, tool, error): ...
    def log_tool_call_denied(self, exec_id, tool, role, reason): ...
    # ... etc for all event types
```

---

## Component 9: Base Docker Image + Runtime Pip Install

### Strategy: Thin Base Image + Role Packages at Runtime

Instead of one fat image with every package or multiple per-role images, we use a single
lightweight base image. Role-specific packages are installed via `pip install` when the
container is created (~10-15s, async during agent startup). This gives:

- **1 Dockerfile** to maintain (not 7)
- **Easy iteration** — change packages by editing a Python dict, no image rebuild
- **Shared Docker layers** — all containers share the base image on disk
- **Upgrade path** — once package lists stabilize, freeze into per-role images

### Research Validation

The package selection is grounded in ecosystem research (see `.claude/research/ptc-package-ecosystem.md`):

- **Anthropic's own sandbox** includes: pandas, numpy, scipy, scikit-learn, statsmodels, matplotlib, seaborn, sympy, pillow
- **OpenAI Code Interpreter** includes 400+ packages across data science, ML, NLP, and visualization
- **E2B** includes pandas, numpy, scikit-learn, scipy, matplotlib, seaborn, plotly, nltk, spacy
- **LLM-in-Sandbox paper** (arXiv:2601.16206): Strong models used code execution 43.4% of the time for numerical verification; achieved 8x token reduction by processing data in sandbox; showed +0.5% to +24.2% accuracy gains with sandbox access

**Key insight from research:** "Strong LLMs, without additional training, exhibit generalization capabilities to leverage the code sandbox for non-code tasks." Having packages available fundamentally changes agent behavior.

### Base Image (`Dockerfile`)

```dockerfile
FROM python:3.11-slim

# System tools needed by all roles
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Base packages: only what EVERY role needs (~minimal)
# Role-specific packages installed at runtime via pip
RUN pip install --no-cache-dir \
    pyyaml toml \
    pytest ruff

# Create non-root user
RUN useradd -m -s /bin/bash ptcuser
USER ptcuser
WORKDIR /workspace

# No entrypoint — container kept alive with "sleep infinity"
# REPLs started via "docker exec" when needed
```

Base image size: ~80-100 MB (python:3.11-slim + git + minimal pip packages).
Role-specific packages add 8-35 MB each via runtime pip install.

### Per-Role Package Rationale

Packages listed in `ROLE_PACKAGES` (Component 6) are selected per-agent based on actual
PTC usage patterns. See `ptc-skill-integration-analysis.md` for detailed per-agent analysis.

**Explorer packages (~25 MB installed):**

| Package | Size | Why Explorer Needs It |
|---------|------|----------------------|
| tree-sitter + grammars | ~15 MB | Multi-language AST parsing (not just Python) |
| ast-grep-py | ~8 MB | Structural code search ("find all callers of X") |
| jedi | ~2 MB | Type inference, goto-definition without running code |
| radon | ~200 KB | Cyclomatic complexity, maintainability index |
| vulture | ~100 KB | Dead code detection |
| networkx | ~3 MB | Dependency graphs, PageRank centrality, cycle detection |
| pandas | ~30 MB | Tabulation, aggregation of analysis results |
| cognitive-complexity | ~30 KB | Human-difficulty metric (SonarSource) |
| lizard | ~300 KB | Multi-language complexity (C++, Java, Go, Rust) |
| cohesion | ~30 KB | Class cohesion measurement |
| gitpython | ~1.5 MB | Programmatic git blame, commit history |
| pydriller | ~200 KB | Repository mining (churn, contributor analysis) |
| pyan3 | ~100 KB | Static call graph generation |
| tiktoken | ~2 MB | Token budget awareness for context packets |

**Coder packages (~15 MB installed):**

| Package | Size | Why Coder Needs It |
|---------|------|-------------------|
| pyinstrument | ~2 MB | Wall-time profiling after green phase |
| pympler | ~500 KB | Deep object size measurement (memory efficiency) |
| line-profiler | ~3 MB | Line-by-line CPU time profiling |
| perflint | ~50 KB | Static detection of performance anti-patterns |
| radon + cognitive-complexity | ~230 KB | Complexity check on own code |
| coverage + pytest-cov | ~300 KB | Test coverage analysis |
| diff-cover | ~100 KB | Coverage specifically on changed lines |
| bandit | ~300 KB | Security vulnerability scanning |
| unidiff | ~50 KB | Programmatic diff parsing for scope checking |
| tiktoken | ~2 MB | Token budget awareness |

**Auditor packages (~20 MB installed):**

| Package | Size | Why Auditor Needs It |
|---------|------|---------------------|
| radon + cognitive-complexity | ~230 KB | Complexity regression detection (before vs after) |
| vulture + cohesion | ~130 KB | Dead code, class quality |
| coverage + diff-cover | ~350 KB | Coverage on changed lines (highest-value audit metric) |
| bandit | ~300 KB | Security scanning on changed code |
| unidiff | ~50 KB | Structured diff parsing |
| networkx | ~3 MB | Blast radius analysis (import graph out-degree) |
| pydriller | ~200 KB | Commit churn analysis (churn correlates with bugs) |
| wily | ~200 KB | Complexity trend over phase commits |
| tiktoken | ~2 MB | Token budget awareness |

**Tester packages (~35 MB installed, numpy is the bulk):**

| Package | Size | Why Tester Needs It |
|---------|------|-------------------|
| hypothesis | ~1 MB | Property-based test generation (Tier 4 scenarios) |
| coverage + pytest-cov | ~300 KB | Coverage gap analysis |
| pyinstrument | ~2 MB | Test performance profiling |
| big-o | ~50 KB | Empirical Big-O complexity verification (needs numpy ~30 MB) |
| memray | ~4 MB | Memory leak detection under sustained load |
| radon + cognitive-complexity | ~230 KB | Complexity metrics |
| tiktoken | ~2 MB | Token budget awareness |

**Researcher packages (~8 MB installed):**

| Package | Size | Why Researcher Needs It |
|---------|------|------------------------|
| trafilatura | ~2 MB | Best-in-class web content extraction (strips noise) |
| html2text | ~100 KB | HTML→Markdown conversion (zero dependencies) |
| markdownify | ~30 KB | Alternative HTML→Markdown preserving structure |
| readability-lxml | ~50 KB | Mozilla's "main content" extraction algorithm |
| pypdf | ~1 MB | PDF text extraction (research papers, specs) |
| pdfplumber | ~500 KB | PDF with table extraction |
| markdown-it-py | ~300 KB | Markdown parsing for structured section extraction |
| rapidfuzz | ~2 MB | Fuzzy string matching for source deduplication |
| pandas | ~30 MB | Comparison tables across research sources |
| tiktoken | ~2 MB | Token budget awareness |

**Strategist packages (~5 MB installed, lightest role):**

| Package | Size | Why Strategist Needs It |
|---------|------|------------------------|
| networkx | ~3 MB | Task dependency DAG validation, cycle detection |
| radon + cognitive-complexity | ~230 KB | Per-task complexity estimation |
| tiktoken | ~2 MB | Measure token cost of plan (loaded into every coder) |

### Hardening Packages (On-Demand Only)

These are heavy and only needed during final production audit. Installed via runtime
pip when the auditor enters HARDENING mode. Not part of any role's default packages.

| Package | Size | When |
|---------|------|------|
| semgrep | ~55-60 MB | Final security/correctness audit (5000+ rules) |
| pip-audit | ~200 KB | Dependency vulnerability check |
| scalene | ~5-10 MB | Combined CPU+memory profiler for full integration tests |

Installed: `await _docker_exec(container, "pip install --no-cache-dir semgrep pip-audit scalene")`
Takes ~30 seconds. Happens once per project, not per phase.

### Domain-Specific Packages (Optional Layer)

For robotics-cv domain, add to any role's container on demand:
```python
DOMAIN_PACKAGES = {
    "robotics-cv": [
        "opencv-python-headless",
        "scikit-image",
        "scipy",
        "torch --index-url https://download.pytorch.org/whl/cpu",
        "torchvision --index-url https://download.pytorch.org/whl/cpu",
    ],
    "data-engineering": ["polars", "duckdb", "sqlalchemy", "pandera"],
    "web-dev": ["flask", "fastapi", "cssutils", "html5lib"],
}
```

Alternatively, for heavy domain packages (torch ~700 MB), use a domain-specific
Dockerfile that extends the base:
```dockerfile
FROM ptc-sandbox:latest
RUN pip install --no-cache-dir opencv-python-headless scikit-image ...
```

### Build Script (`build-image.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== PTC Sandbox Base Image Builder ==="

# Build base image (thin — role packages installed at runtime)
echo "[1/2] Building base image (ptc-sandbox:latest)..."
docker build -t ptc-sandbox:latest -f Dockerfile .

# Verify base image
echo "[2/2] Verifying base image..."
docker run --rm ptc-sandbox:latest python3 -c "
import json, sys
# Base only has stdlib + minimal packages
checks = {'json': True, 'ast': True, 'os': True}
for name in ['yaml', 'pytest', 'ruff']:
    try:
        __import__(name if name != 'yaml' else 'yaml')
        checks[name] = True
    except ImportError:
        checks[name] = False
missing = [k for k, v in checks.items() if not v]
if missing:
    print(f'ERROR: Missing: {missing}', file=sys.stderr)
    sys.exit(1)
print(json.dumps({'status': 'ok', 'python': sys.version.split()[0]}))
"

echo "=== Base image ready. Role packages installed at container runtime. ==="
docker images --filter reference='ptc-sandbox:*' --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}'
```

---

## Component 10: Config (`config.json`)

```json
{
  "role_tools": {
    "explorer": ["execute", "read_file", "list_dir", "search_code", "analyze_imports", "count_tokens"],
    "researcher": ["execute", "web_fetch_summary", "mcp_call_summary"],
    "coder": ["execute", "read_file", "write_file", "run_tests", "run_linter", "git_diff_summary"],
    "tester": ["execute", "read_file", "run_tests", "coverage_summary"],
    "auditor": ["execute", "read_file", "search_code", "diff_summary", "list_changes"],
    "strategist": ["execute", "read_file", "analyze_structure"],
  },
  "tool_strategies": {
    "read_file": "ptc_tools",
    "list_dir": "ptc_tools",
    "search_code": "ptc_tools",
    "analyze_imports": "ptc_tools",
    "analyze_structure": "ptc_tools",
    "count_tokens": "ptc_tools",
    "run_tests": "subprocess",
    "run_linter": "subprocess",
    "coverage_summary": "subprocess",
    "git_diff_summary": "subprocess",
    "diff_summary": "subprocess",
    "list_changes": "subprocess",
    "write_file": "host_fs",
    "web_fetch_summary": "host_network",
    "mcp_call_summary": "mcp_proxy"
  },
  "resource_limits": {
    "memory_mb": 1024,
    "cpu_cores": 1,
    "timeout_seconds": 60,
    "max_containers": 8,
    "disk_mb": 512,
    "max_output_bytes": 65536,
    "idle_timeout_seconds": 300,
    "max_tool_calls_per_execution": 50
  },
  "repl_pool": {
    "max_repls_per_container": 6,
    "repl_start_timeout_seconds": 5,
    "repl_idle_timeout_seconds": 60,
    "pip_install_timeout_seconds": 120
  },
  "docker": {
    "image": "ptc-sandbox:latest",
    "network_mode": "none",
    "container_prefix": "ptc"
  },
  "ipc": {
    "socket_dir": "/tmp/ptc-ipc",
    "socket_permissions": "0o666",
    "connect_timeout_seconds": 10,
    "tool_call_timeout_seconds": 30
  },
  "observability": {
    "log_file": "~/.claude/logs/ptc-events.jsonl",
    "debug_log_code": false,
    "result_preview_chars": 200
  }
}
```

---

## Error Handling

| Error | Detection | Recovery |
|-------|-----------|----------|
| Container crash during execution | Socket EOF | Return error to agent. NEVER re-execute. Agent retries if needed. |
| Container crash during idle | Ping timeout on next call | Restart container, namespace lost, warn agent. |
| REPL crash (sub-agent) | Socket EOF on REPL-N | Kill REPL-N only. Container + other REPLs unaffected. Sub-agent gets error. |
| REPL crash (parent) | Socket EOF on REPL-0 | Start fresh REPL-0. Namespace lost for parent, sub-agent REPLs unaffected. |
| Tool call timeout | `asyncio.wait_for` per call | Send error result to container, code gets `ToolError`. |
| Overall execution timeout | Deadline timer | Send `CancelMsg`, interrupt code, return timeout error. |
| Malformed IPC message | `JSONDecodeError` | Log error, send error result to container. |
| Container OOM | Docker kills it | Detected as crash. ALL REPLs lost. Log `container_crash` with `oom_killed`. |
| Code syntax error | `compile()` raises | Return `ErrorMsg` with traceback. |
| Max tool calls exceeded | Counter check | Send cancel, return partial result. |
| Docker unavailable | `docker info` check at startup | `ptc_execute` returns clear error. Agents use native tools. |
| Permission denied | Registry check | Return error with role and tool name. Log `tool_call_denied`. |
| Namespace too large | Size check after each exec | Warn at 256MB, auto-evict oldest non-callable objects at 512MB. |
| Max REPLs exceeded | len(container.repls) check | Return error. Sub-agent falls back to non-PTC tools. |
| Pip install timeout | asyncio.wait_for on pip | Return error. Container created but unusable. Agent uses native tools. |
| Pip install failure | Non-zero exit from pip | Log error with package name. Container usable for stdlib-only code. |

### Crash Recovery Detail

```
Container crashes mid-execution (between tool_call and tool_result):
1. Host IpcHost detects socket EOF
2. All pending futures rejected with ContainerCrashError
3. Log container_crash event
4. Return: {"error": "Container crashed during execution", "recoverable": true}
5. Agent decides whether to retry
6. NEVER auto-re-execute — code may have had side effects

Container crashes during idle (between ptc_execute calls):
1. Next ptc_execute detects stale container via ping timeout
2. Container restarted automatically
3. Namespace is LOST (fresh start)
4. Execution proceeds normally
5. Agent receives: {"_warning": "Container restarted, namespace reset"}
```

---

## Implementation Phases

### Phase 1: IPC Protocol + Sandbox Runtime
**New files:** `ipc_protocol.py`, `sandbox_runtime/runtime.py`, `sandbox_runtime/executor.py`, `ipc_sandbox.py`
**Tests:** `test_ipc_protocol.py`, `test_sandbox_runtime.py`

1. Define all message types as dataclasses with NDJSON encode/decode
2. Build executor with persistent namespace, stdout capture, await wrapping
3. Build runtime entry point — accepts `--socket` arg for REPL pool support
4. Build container-side IPC client (send tool_call, receive tool_result)
5. Verify: in-process test — executor runs code, captures output, namespace persists

### Phase 2: IPC Host + Container Manager + REPL Pool
**New files:** `ipc_host.py`, `container_manager.py`, `event_logger.py`
**Tests:** `test_ipc_host.py`, `test_container_manager.py`, `test_event_logger.py`

1. Build host-side socket server with execute → tool_call → complete lifecycle
2. Build container manager with REPL pool:
   - `ContainerInfo` with `repls: dict[str, ReplHandle]`
   - `get_or_create_repl()` routing: existing → parent's container → idle same-role → new
   - `_start_repl_in_container()` — docker exec for new REPL process
   - `handle_handoff()` — persist container, reset REPL
   - `register_parent()` — sub-agent → parent mapping
   - `ROLE_PACKAGES` dict for runtime pip install
3. Build event logger with all event types (including REPL pool events)
4. Verify: create container, pip install role packages, start multiple REPLs,
   execute code independently in each, see events in JSONL

### Phase 3: Tool Dispatch + ptc_tools
**New files:** `tool_dispatcher.py`, `ptc_tools/` (7 files)
**Tests:** `test_tool_dispatcher.py`, `test_ptc_tools.py`

1. Refactor `tool_implementations/` `fallback()` functions into `ptc_tools/` modules
2. Build tool dispatcher with strategy routing and semaphore backpressure
3. Integrate dispatch into IPC host's execute loop
4. Verify: agent code `await read_file(...)` → IPC → host dispatch → result back

### Phase 4: MCP Server + Docker Image
**Modified files:** `server.py`, `config.json`, `tool_registry.py`, `requirements.txt`
**New files:** `Dockerfile`, `build-image.sh`
**Tests:** `test_ptc_execute.py`

1. Rewrite server.py with single ptc_execute + lifecycle tools
2. Build thin base Docker image (python:3.11-slim + git + minimal pip)
3. Update config.json with REPL pool settings, role_tools, tool_strategies
4. Update tool_registry for execute permission and strategies
5. Verify: end-to-end MCP call → container → pip install → REPL → tool dispatch → result

### Phase 5: Error Handling + Cleanup
**Tests:** additions to existing test files

1. Crash recovery (container death, REPL death — separate handling)
2. Timeout handling (per-tool, overall deadline, max calls, pip install timeout)
3. Namespace monitoring and eviction
4. Max REPLs per container enforcement
5. Pip install failure handling (partial functionality)
6. Remove `make_kernel_code()` from all 15 tool_implementations
7. Delete old test files (`test_kernel_manager.py`, `test_server.py`, `test_tools.py`)
8. Update `requirements.txt`

### Phase 6: PTC Skills + Sub-Agent Delegation
**New files:** `skills/ptc-sandbox/SKILL.md`, `skills/ptc-sandbox/role-specs/*.md`,
`skills/ptc-sandbox/references/*.md`, `skills/ptc-sandbox/specializations/*.md`
**Modified files:** `skills/sub-agent-delegation/SKILL.md` (add PTC delegation section)

1. Write `ptc-sandbox` SKILL.md — universal mechanics (~900 tokens)
2. Write per-role specs (explorer, coder, auditor, tester, researcher, strategist)
3. Write reference docs (tool-calling-patterns, anti-patterns)
4. Add PTC delegation section to `sub-agent-delegation` skill
5. Write domain specialization (robotics-cv)
6. Verify: agent loads skill, role-spec auto-loaded, sub-agent delegation includes PTC instructions

---

## Verification Checklist

### Core IPC
1. **IPC round-trip:** Host sends `execute` → container executes → tool call pauses → host dispatches → result returns → code resumes → `complete` returns
2. **Parallel tools:** `asyncio.gather(tool1(), tool2())` fires concurrent IPC calls, both resolve
3. **Backpressure:** 8 parallel tool calls with semaphore(4) — only 4 dispatch concurrently
4. **Persistent state:** Variable set in call 1 accessible in call 2
5. **Namespace eviction:** Namespace exceeding 512MB auto-evicts oldest non-callable objects
6. **Role enforcement:** Explorer can't call `run_tests`, orchestrator can't call `ptc_execute`
7. **Timeout:** Code running longer than timeout gets cancelled via IPC
8. **Output cap:** 64KB limit enforced, truncation flag set

### Container Management
9. **Base image build:** `build-image.sh` creates verified base image
10. **Runtime pip install:** Container creation installs role-specific packages within timeout
11. **Eager startup:** Container + pip install + REPL ready before first ptc_execute call
12. **Package availability:** Role-specific packages importable after runtime install
13. **ptc_tools importable:** `from ptc_tools.search import search_code` works inside container
14. **Docker unavailable:** Clear error, agents use native tools

### REPL Pool
15. **REPL start:** New REPL process starts in existing container in <500ms
16. **Multiple REPLs:** 3 REPLs in one container execute independently and concurrently
17. **REPL isolation:** Variables in REPL-0 not visible in REPL-1 (separate namespaces)
18. **Sub-agent routing:** Sub-agent's ptc_execute routes to parent's container (new REPL)
19. **REPL cleanup:** Sub-agent terminates → its REPL killed, container unaffected
20. **Max REPLs:** Container rejects new REPL when at max_repls_per_container limit

### Handoff & Lifecycle
21. **Handoff preserves container:** Old agent hands off → container persists (no reinstall)
22. **Handoff resets namespace:** Fresh REPL started for replacement agent (clean state)
23. **Container reuse:** Idle same-role container reused by new agent (skip pip install)
24. **Idle timeout:** Container destroyed after idle_timeout_seconds with no active REPLs

### Error Recovery
25. **Crash during execution:** Socket EOF → error returned (no re-execution)
26. **Crash during idle:** Container restarted, namespace lost, warning returned
27. **REPL crash:** Individual REPL dies → only that agent affected, container + other REPLs survive

### Observability
28. **All events in JSONL:** Container, REPL, execution, tool call events logged
29. **REPL events:** `repl_start`, `repl_stop` events with agent_id and container correlation
30. **Queryable with jq:** Standard event envelope with correlation IDs

## Resource Requirements

| Resource | 5 Containers (typical) | 8 Containers (max) |
|----------|----------------------|-------------------|
| RAM | ~1-2 GB | ~2-4 GB |
| CPU | 5 cores (soft limit) | 8 cores (soft limit) |
| Disk (tmpfs) | 2.5 GB | 4 GB |
| Docker base image | ~80-100 MB (python:3.11-slim + git) | Same (shared layer) |
| Per-container pip install | +8-35 MB (role-dependent) | Same |
| Idle container (no REPLs) | ~20-30 MB | ~20-30 MB |
| Active REPL process | ~15-20 MB per REPL | ~15-20 MB per REPL |
| Typical container (1 REPL + packages) | ~50-100 MB | ~50-100 MB |
| Explorer container (3 sub-agent REPLs) | ~120-150 MB | ~120-150 MB |

Note: REPL processes share package code pages via OS page cache. 4 REPLs in one
container ≠ 4× memory. Shared packages counted once, per-process heap ~10-15 MB each.

---

## PTC Skill Design

PTC knowledge is split into two layers:

- **Layer 1 (Mechanics):** Universal — how ptc_execute works, print discipline, available
  tools, anti-patterns. Lives in a standalone `ptc-sandbox` skill. Loaded by all agents
  with `execute` permission. ~900 tokens.

- **Layer 2 (Recipes):** Role-specific — what PTC code to write for each agent's job.
  Lives in each agent's primary skill (e.g., `codebase-exploration` has explorer PTC recipes,
  `task-execution` has coder PTC recipes). Agents reference `ptc-sandbox` for mechanics,
  provide domain-specific patterns inline.

Additionally, `sub-agent-delegation` teaches how to delegate PTC-enabled tasks to sub-agents.

### Standalone Skill: `ptc-sandbox`

Location: `~/personal/agentic_workflow/skills/ptc-sandbox/`

```
skills/ptc-sandbox/
  SKILL.md                           # Universal mechanics (~900 tokens)
  references/
    tool-calling-patterns.md         # await, asyncio.gather, error handling, loops
    anti-patterns.md                 # What NOT to do (printing raw data, repeated single calls)
  role-specs/
    explorer.md                      # Explorer's available packages + PTC recipes
    coder.md                         # Coder's available packages + PTC recipes
    auditor.md                       # Auditor's available packages + PTC recipes
    tester.md                        # Tester's available packages + PTC recipes
    researcher.md                    # Researcher's available packages + PTC recipes
    strategist.md                    # Strategist's available packages + PTC recipes
  specializations/
    robotics-cv.md                   # Domain packages: opencv, torch, scipy patterns
    data-engineering.md              # Domain packages: polars, duckdb, sqlalchemy patterns
```

### SKILL.md — Universal Mechanics (~900 tokens)

```yaml
---
name: ptc-sandbox
description: Execute Python code in persistent sandboxed containers with tool calling
triggers:
  - agent has "execute" permission in role_tools
  - agent needs to analyze multiple files
  - agent needs to process data and return summaries
loads_role_spec: true  # auto-loads role-specs/{role}.md based on agent role
---
```

**Body:**

1. **ptc_execute API:**
   ```
   ptc_execute(agent_id: str, role: str, code: str, timeout: int = 0) -> str
   ```
   - `code`: Python code to execute. Top-level `await` is supported.
   - Returns: JSON string with `stdout`, `stderr`, `return_code`, `namespace_keys`.
   - Only `print()` output enters your context. Everything else stays in the container.

2. **Available tools** — async functions callable via `await` inside your code:
   ```python
   data = await read_file(path="src/main.py")
   results = await run_tests(test_path="tests/")
   files = await list_dir(path="src/", max_depth=2)
   ```

3. **Print discipline** — THE critical skill for token savings:
   ```python
   # GOOD: 400KB stays in container, 200 bytes in context
   import ast, json
   data = await read_file(path="src/models.py")
   tree = ast.parse(data["content"])
   classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
   print(json.dumps({"classes": classes, "count": len(classes)}))

   # BAD: 400KB enters context
   data = await read_file(path="src/models.py")
   print(data["content"])  # DON'T DO THIS — defeats the purpose of PTC
   ```

4. **Parallel tool calls** — use `asyncio.gather()`:
   ```python
   import asyncio
   test_result, lint_result = await asyncio.gather(
       run_tests(test_path="tests/"),
       run_linter(paths="src/"),
   )
   ```

5. **State persists** — variables from previous `ptc_execute` calls survive:
   ```python
   # Call 1: data = await read_file(path="src/main.py")
   # Call 2: print(len(data["content"]))  # works — same namespace
   ```

6. **Anti-patterns:**
   - Don't print raw file contents — process them first, print summaries
   - Don't make separate ptc_execute calls for each file — batch in one call with a loop
   - Don't ignore available packages — use ast for parsing, networkx for graphs, pandas for tabulation
   - Don't use PTC for simple single-file reads — use the Read tool instead (faster, no container overhead)
   - Don't write code that exceeds the timeout — break into smaller ptc_execute calls

7. **Error handling:**
   ```python
   try:
       data = await read_file(path="nonexistent.py")
   except ToolError as e:
       print(json.dumps({"error": str(e)}))
   ```

8. **Available packages:** See your role-spec (auto-loaded) for the exact list.
   Your container has packages specific to your role installed at startup.

### Per-Role Specs (`role-specs/*.md`)

Each role spec is auto-loaded based on the agent's role. It tells the agent exactly what
packages are available and provides concrete PTC recipes for that role's tasks.

#### `role-specs/explorer.md` (~400 tokens)

```markdown
## Your PTC Packages (Explorer)

Code analysis: ast (stdlib), tree-sitter, ast-grep-py, jedi, pyan3
Metrics: radon, vulture, cognitive-complexity, lizard, cohesion
Graphs: networkx
Data: pandas
Git: gitpython, pydriller
Tokens: tiktoken

## When to Use PTC (Explorer)

- **Full codebase analysis:** One ptc_execute reads ALL files, parses with ast/tree-sitter,
  builds dependency graph with networkx, computes metrics with radon, prints structured JSON.
  Replaces 50+ individual Read calls.
- **Surgical query:** "What does function X call?" → jedi for type inference, pyan3 for
  call graph, ast for signature extraction.
- **Git history:** pydriller for churn analysis, gitpython for blame-based ownership.
- **Token budget:** tiktoken to measure context packet size before delivery.

## When NOT to Use PTC (Explorer)

- Single file read → use Read tool
- Simple grep → use Grep tool
- Listing directory → use Glob tool

## Self-Serve vs Delegation

You can answer most queries yourself in one ptc_execute call. Sub-agents are needed
only for very large codebases (10K+ files) where parallel analysis is faster.
When delegating: sub-agents share YOUR container (REPL pool). See sub-agent-delegation.
```

#### `role-specs/coder.md` (~400 tokens)

```markdown
## Your PTC Packages (Coder)

Profiling: pyinstrument, pympler, line-profiler
Quality: perflint, radon, cognitive-complexity
Testing: coverage, pytest-cov, diff-cover
Security: bandit
Diffs: unidiff
Tokens: tiktoken

## When to Use PTC (Coder)

- **Pre-implementation analysis:** ast-parse the target module to understand existing
  signatures, patterns, and complexity before writing code.
- **Resource efficiency check (after green phase, before quality gate):**
  pyinstrument for hot spots, pympler for memory, cognitive-complexity for readability.
- **Scope verification:** unidiff parses git diff, compare modified files/functions
  against plan's touched_files. Automated plan adherence.
- **Quality gate batching:** Run ruff format + ruff check + pyright in one ptc_execute,
  return structured pass/fail summary instead of raw output.
- **Simple structural queries:** "What are the function signatures in auth.py?" →
  ast-parse directly instead of round-tripping through orchestrator → explorer.

## When NOT to Use PTC (Coder)

- Writing code → use Write/Edit tools (host filesystem)
- Complex codebase questions → message orchestrator, who dispatches explorer
```

#### `role-specs/auditor.md` (~400 tokens)

```markdown
## Your PTC Packages (Auditor)

Metrics: radon, cognitive-complexity, vulture, cohesion
Coverage: coverage, pytest-cov, diff-cover
Security: bandit
Diffs: unidiff, pydriller, wily
Graphs: networkx
Tokens: tiktoken

## When to Use PTC (Auditor)

- **Task audit (after every coder claims done):**
  1. unidiff parses git diff → scope violation check against plan
  2. diff-cover → "what % of new code is tested?" (highest-value audit metric)
  3. cognitive-complexity → complexity regression detection
  4. ast scan test files → detect fraudulent tests (zero assertions)
  One ptc_execute, ~400 tokens returned, replaces 5-20K tokens of manual reading.

- **Phase audit:**
  Full coverage report + pydriller churn analysis + vulture dead code scan.

- **Hardening (final audit):**
  Runtime install semgrep + pip-audit. Comprehensive security + dependency scan.
  Takes ~30s install, runs once per project.

- **Arbitration:**
  coverage + hypothesis for independent property-based verification.
  Neither tester's nor coder's tests — auditor's own.

## Mandatory PTC Checks (Always Run)

Task-level: scope check + diff-cover + complexity regression + test assertion count.
These are not optional. Data-backed verdicts, not opinions.
```

#### `role-specs/tester.md` (~350 tokens)

```markdown
## Your PTC Packages (Tester)

Testing: hypothesis, coverage, pytest-cov
Profiling: pyinstrument, big-o, memray
Metrics: radon, cognitive-complexity
Tokens: tiktoken

## When to Use PTC (Tester)

- **Tier 4 property-based scenarios:** hypothesis generates inputs from strategies,
  finds counterexamples automatically. This tier is impossible without PTC.
- **Coverage gap analysis:** coverage + ast identifies which functions are untested.
- **Flakiness detection:** Run test suite 5x in container, compare results.
- **Performance verification:** big-o empirically estimates complexity.
  "Spec says O(n log n)" → big-o confirms or refutes.
- **Memory testing:** memray detects leaks under sustained load (run scenarios in loop).

## Blind Wall Constraint

You work from design doc + plan + context packets ONLY. PTC runs on the merged
codebase but you never read individual implementation files. You write scenario
tests that exercise behavior, not implementation details.
```

#### `role-specs/researcher.md` (~250 tokens)

```markdown
## Your PTC Packages (Researcher)

Extraction: trafilatura, html2text, markdownify, readability-lxml
Documents: pypdf, pdfplumber, markdown-it-py
Matching: rapidfuzz
Data: pandas
Tokens: tiktoken

## When to Use PTC (Researcher)

- **Clean web results:** trafilatura strips nav/ads/boilerplate from raw HTML.
  Process HTML in container, print clean text summary.
- **Deduplicate sources:** rapidfuzz detects >80% similar content across sources.
  Corroborated sources → higher confidence score.
- **Parse PDFs:** pypdf/pdfplumber extract text from papers/specs without full
  document entering context.
- **Compare libraries:** pandas pivot tables for feature comparison across sources.

## When NOT to Use PTC (Researcher)

- The LLM is already great at NLP. Don't install spaCy/NLTK — you handle language natively.
- Simple MCP queries → call Context7/WebSearch directly, process results in context.
```

#### `role-specs/strategist.md` (~200 tokens)

```markdown
## Your PTC Packages (Strategist)

Graphs: networkx
Metrics: radon, cognitive-complexity
Tokens: tiktoken

## When to Use PTC (Strategist)

- **Plan validation (before shipping):**
  1. networkx checks task dependency DAG for cycles, suggests topological order
  2. ast verifies touched_functions actually exist in the codebase
  3. radon estimates per-task complexity from the files each task touches
  4. tiktoken measures token cost of the plan (it loads into every coder's context)

## The strategist uses PTC the LEAST of any agent.

Planning is reasoning, not computing. PTC helps with validation only.
One ptc_execute call at plan completion. That's typically it.
```

### Sub-Agent PTC Delegation (in `sub-agent-delegation` skill)

The `sub-agent-delegation` skill gets a new section teaching agents how to delegate
PTC-enabled tasks to sub-agents. Sub-agents share the parent's container (REPL pool)
and receive inline PTC instructions — they don't need the full ptc-sandbox skill.

#### Addition to `sub-agent-delegation` SKILL.md:

```markdown
## PTC Delegation Patterns

### When to Delegate PTC Work to Sub-Agents

Delegate when the task has multiple independent parts that each require LLM reasoning
at intermediate steps. If the task is purely mechanical (no reasoning needed between
steps), write one ptc_execute call with a loop instead.

Examples:
- DELEGATE: "Analyze structure, coverage, and dependencies" — 3 independent analyses,
  each may need judgment ("this function is suspiciously complex, dig deeper")
- DON'T DELEGATE: "Read 50 files and extract class names" — purely mechanical,
  one ptc_execute with os.walk handles it

### How Sub-Agents Get PTC Access

Sub-agents share YOUR container. The kernel_manager starts a new REPL process
(~100ms) in your container when a sub-agent calls ptc_execute. No new container,
no pip install. The sub-agent gets the same packages you have.

### What to Include in the Delegation Prompt

When delegating a PTC task, include in the sub-agent's Task prompt:

1. **PTC availability:** "You have access to ptc_execute for running analysis code."
2. **Available packages:** List the RELEVANT subset (not all packages — only what this
   sub-agent needs for its specific task).
3. **Print discipline:** "Only print() output returns to your context. Process data
   in the container, print a JSON summary."
4. **Specific task:** Exactly what to analyze and what structure to output.
5. **Output location:** Where to write results (file path).

### Delegation Template

```python
Task(
    prompt="""Analyze test coverage for the payment module.

    You have access to ptc_execute for running analysis code.
    Available packages: coverage, pytest-cov, ast, json (stdlib)
    Project is mounted at /workspace.

    Write a ptc_execute call that:
    1. Runs pytest with coverage on tests/test_payment*.py
    2. Parses the coverage report
    3. Identifies uncovered functions in src/payment/
    4. Prints a JSON summary with: total_coverage_pct, uncovered_functions list

    Write your results to: .claude/context/queries/payment-coverage.json
    """,
    subagent_type="general-purpose",
    model="haiku",  # cheap, mechanical task with clear instructions
    team_name="my-team"
)
```

### Sub-Agent PTC Anti-Patterns

- Don't tell sub-agents to "use PTC for everything" — simple file reads use Read tool
- Don't list all packages — only the 3-5 the sub-agent actually needs
- Don't delegate PTC tasks to sub-agents when one ptc_execute call suffices
- Don't spawn sub-agents for tasks that don't need LLM reasoning (use ptc_execute directly)
```

### Domain Specialization (`specializations/robotics-cv.md`)

Loaded when `Project Domain: robotics-cv` is declared. Adds domain-specific packages
and patterns to any role-spec that needs them:
- opencv patterns for image analysis in tests
- numpy/scipy for transformation matrices, sensor data
- torch patterns for model inference verification
- matplotlib for trajectory visualization

These packages are NOT in the base ROLE_PACKAGES. They are added to the relevant
role's runtime pip install when the domain is active:
```python
DOMAIN_PACKAGES = {
    "robotics-cv": ["opencv-python-headless", "scikit-image", "scipy", ...],
}
```

---

## Deferred (Future Work)

- MCP proxy dispatch (Context7, GitHub from inside sandbox)
- Token savings measurement (aggregate from JSONL events)
- Per-role Dockerfiles (freeze ROLE_PACKAGES into images once stabilized — eliminates pip install time)
- In-process tool dispatch (eliminate IPC for ptc_tools by mounting as importable)
- Container image registry (push to registry for team sharing)
- Log rotation / retention policy
- TUI dashboard for PTC events (ties into Doc 4 observability)
- Shared base package layer in image (move commonly-shared packages like tiktoken, radon into Dockerfile)
