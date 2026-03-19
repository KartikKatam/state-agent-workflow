# PTC Implementation Plan — Plaintext

**Feature:** PTC (Programmatic Tool Calling) MCP Server Rewrite
**Design Doc:** `.claude/designs/ptc.md`
**Context Packet:** `.claude/context/ptc-context.json`
**Status:** Draft
**Date:** 2026-02-28
**Chunks:** 14

---

## Overview

Replace the 15-tool MCP server at `~/.claude/mcp/ptc-server/` with a single `ptc_execute` tool backed by:
- NDJSON IPC over Unix domain sockets
- Persistent Docker containers with REPL pool
- Host-side tool dispatch with strategy routing
- Runtime pip install for role-specific packages
- JSONL observability for all events

The design doc defines 6 implementation phases. This plan converts them into 14 TDD-implementable chunks with explicit dependencies, invariants, and scope.

---

## Dependency Graph

```
chunk-01 (IPC Protocol)        chunk-04 (Event Logger)        chunk-08 (ptc_tools core)
    │                              │                              │
    ├──→ chunk-02 (Executor)       │                              ├──→ chunk-09 (ptc_tools subprocess)
    │        │                     │                              │         │
    │        └──→ chunk-03 (Runtm) │                              └──→ chunk-10 (Tool Dispatcher)
    │                │             │                                   │   │
    └────────────────┴─────────────┴──→ chunk-05 (IPC Host)            │   │
                                            │                          │   │
                                            └──→ chunk-06 (Cont. Mgr)  │   │
                                                      │                │   │
                                                      └──→ chunk-07 (REPL Pool)
                                                                │      │   │
    chunk-11 (Docker+Config+Registry) ←─────────────────────────┘      │   │
         ↑ also depends on chunk-08, chunk-09                          │   │
         │                                                             │   │
         └──→ chunk-12 (MCP Server) ←──────────────────────────────────┘───┘
                   ↑ also depends on chunk-06, chunk-07
                   │
                   └──→ chunk-13 (Error Handling + Cleanup)

    chunk-14 (PTC Skills) — no code deps, can be done anytime
```

**Parallelizable groups:**
- chunk-01, chunk-04, chunk-08 — all independent, can start in parallel
- chunk-02 + chunk-09 — can run in parallel after their respective deps
- chunk-14 can be done at any point (documentation only)

---

## chunk-01: IPC Protocol Messages and Serialization

**Purpose:** foundational
**Delivers:** All IPC message types can be instantiated, serialized to NDJSON, and deserialized back with unique correlation IDs.
**Dependencies:** none

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/ipc_protocol.py` (create)
- Tests: `tests/ptc/test_ipc_protocol.py` (create)
- Conftest: `tests/ptc/conftest.py` (create — initial version with basic fixtures)

**Tasks:**
1. Define 4 host→container dataclasses: `ExecuteMsg(exec_id, code)`, `ToolResultMsg(call_id, content, error)`, `CancelMsg(exec_id, reason)`, `PingMsg`
2. Define 4 container→host dataclasses: `ToolCallMsg(call_id, tool, input)`, `CompleteMsg(exec_id, stdout, stderr, return_code, namespace_keys, namespace_size_kb)`, `ErrorMsg(exec_id, message, traceback)`, `PongMsg`
3. Implement `encode(msg) -> bytes` — `json.dumps(asdict(msg), separators=(",",":")) + "\n"` encoded to bytes
4. Implement `decode(line: bytes) -> dict` — `json.loads(line.strip())`
5. Implement `new_call_id() -> str` — `f"c-{uuid.uuid4().hex[:12]}"`
6. Implement `new_exec_id() -> str` — `f"e-{uuid.uuid4().hex[:12]}"`
7. Define `MAX_MESSAGE_SIZE = 1_048_576` (1MB)
8. Write tests: round-trip encode/decode for each message type, ID uniqueness, MAX_MESSAGE_SIZE constant
9. Create initial conftest.py with message fixture factories

**Out of scope:**
- Socket transport (that's chunk-03/05)
- Message routing logic (that's chunk-05)
- Any Docker operations

**Invariants:**
- `inv-01-01` [existence]: All 8 message types importable — `python -c "from ipc_protocol import ExecuteMsg, ToolResultMsg, CancelMsg, PingMsg, ToolCallMsg, CompleteMsg, ErrorMsg, PongMsg"`
- `inv-01-02` [behavioral]: encode/decode round-trip preserves data — `python -c "from ipc_protocol import *; m = ExecuteMsg('e1','x'); assert decode(encode(m))['exec_id'] == 'e1'"`
- `inv-01-03` [behavioral]: Tests pass — `pytest tests/ptc/test_ipc_protocol.py -v`

**Design doc reference:** Component 1 (lines 260-345)

---

## chunk-02: Code Executor with Persistent Namespace

**Purpose:** core_logic
**Delivers:** Python code can be executed in a persistent namespace with stdout capture, await detection/wrapping, and tool stubs injected from an IPC client interface.
**Dependencies:** chunk-01

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/sandbox_runtime/executor.py` (create)
- Also: `~/.claude/mcp/ptc-server/sandbox_runtime/__init__.py` (create)
- Tests: `tests/ptc/test_sandbox_runtime.py` (create — executor portion)

**Tasks:**
1. Create `sandbox_runtime/` package with `__init__.py`
2. Implement `Executor.__init__(self, ipc_client, allowed_tools)` — init persistent `_namespace: dict`, store ipc_client reference
3. Implement `_setup_tool_stubs(tools)` — for each tool name, generate `async def _stub(_name=tool_name, **kwargs): return await self._ipc_client.call_tool(_name, kwargs)` and inject into namespace
4. Implement `execute(code) -> (stdout, stderr, return_code, namespace_keys, namespace_size_kb)`:
   - Redirect `sys.stdout`/`sys.stderr` to `StringIO`
   - `compile(code, '<ptc>', 'exec')` to detect syntax errors early
   - If code contains `await`: wrap in `async def _ptc_main(): ... ; asyncio.run(_ptc_main())`
   - `exec()` in `self._namespace`
   - Extract new locals back to namespace
   - Return captured stdout, stderr, return code, list of namespace keys, namespace size in KB
5. Implement namespace size tracking: `sys.getsizeof` approximation, warn at 256MB
6. Write tests using a mock IPC client: simple code execution, stdout capture, namespace persistence across calls, await wrapping, syntax error handling, namespace size tracking

**Out of scope:**
- Namespace eviction at 512MB (that's chunk-13)
- Socket connections (that's chunk-03)
- Docker containers (that's chunk-06)

**Invariants:**
- `inv-02-01` [existence]: Executor importable with correct signature — `python -c "from sandbox_runtime.executor import Executor; import inspect; sig = inspect.signature(Executor.__init__); assert 'ipc_client' in sig.parameters"`
- `inv-02-02` [behavioral]: Execute code and capture stdout — `python -c "from sandbox_runtime.executor import Executor; e = Executor(None, []); r = ...; # tested via pytest"`
- `inv-02-03` [behavioral]: Namespace persists — tested in test_sandbox_runtime.py
- `inv-02-04` [behavioral]: Tests pass — `pytest tests/ptc/test_sandbox_runtime.py -v -k executor`

**Design doc reference:** Component 3 — executor.py (lines 410-437)

---

## chunk-03: Container-side IPC Client + Runtime Entry Point

**Purpose:** core_logic
**Delivers:** A runtime process can connect to a Unix socket, receive execute commands, run code via the Executor, call tools via IPC, and return results — the complete container-side stack.
**Dependencies:** chunk-01, chunk-02

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/sandbox_runtime/runtime.py` (create)
- Also: `~/.claude/mcp/ptc-server/ipc_sandbox.py` (create)
- Tests: `tests/ptc/test_sandbox_runtime.py` (extend — runtime + IPC client)

**Tasks:**
1. Implement `IpcClient` class in `ipc_sandbox.py`:
   - `connect(socket_path)` — asyncio open_unix_connection
   - `call_tool(tool_name, params) -> dict` — send ToolCallMsg, await matching ToolResultMsg by call_id
   - `_response_handler()` — read incoming messages, route ToolResultMsg to pending futures by call_id
   - Raise `ToolError(msg)` if ToolResultMsg has error field set
2. Implement `runtime.py` main function:
   - Parse `--socket` CLI arg (default: `repl-0.sock`)
   - Connect to socket at `/ptc_ipc/{socket_name}`
   - Create IpcClient + Executor
   - Main loop: read ExecuteMsg → executor.execute(code) → send CompleteMsg or ErrorMsg
   - Handle CancelMsg: interrupt running code (asyncio.Task.cancel)
   - Handle PingMsg: respond with PongMsg
3. Write tests: IpcClient call_tool with mock socket, parallel call routing, ToolError on error result, runtime main loop with mock socket

**Out of scope:**
- Host-side socket server (that's chunk-05)
- Docker containers (that's chunk-06)
- Real socket I/O with Docker (that's integration testing)

**Invariants:**
- `inv-03-01` [existence]: IpcClient importable — `python -c "from ipc_sandbox import IpcClient"`
- `inv-03-02` [existence]: Runtime main importable — `python -c "from sandbox_runtime.runtime import main"`
- `inv-03-03` [behavioral]: Tests pass — `pytest tests/ptc/test_sandbox_runtime.py -v`

**Design doc reference:** Component 3 — runtime.py (lines 394-408), ipc_sandbox.py (lines 439-461)

---

## chunk-04: Event Logger (JSONL Observability)

**Purpose:** foundational (but directly testable — NOT tested_by)
**Delivers:** All PTC events can be logged to JSONL with proper envelope (ts, event, agent_id, exec_id, container, data) and queried with jq.
**Dependencies:** none

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/event_logger.py` (create)
- Tests: `tests/ptc/test_event_logger.py` (create)

**Tasks:**
1. Implement `PtcEventLogger.__init__(log_path)` — create parent dirs, store path
2. Implement `_emit(event, agent_id, exec_id, container, data)` — build envelope dict with ISO timestamp, filter empty strings for compactness, append JSONL line
3. Implement container lifecycle methods: `log_container_create(agent_id, container, role, image, memory_mb)`, `log_container_ready(agent_id, container, startup_ms, pip_install_ms, packages_installed)`, `log_container_stop(agent_id, container, reason, uptime_s, total_executions, total_tool_calls)`, `log_container_crash(agent_id, container, reason, memory_at_crash_mb, active_repls)`, `log_container_health(agent_id, container, ping_latency_ms, memory_mb, cpu_pct, repl_count)`, `log_container_handoff(agent_id, container, old_agent_id, new_agent_id, namespace_reset)`
4. Implement REPL pool methods: `log_repl_start(agent_id, container, repl_index, parent_agent_id, is_sub_agent)`, `log_repl_stop(agent_id, container, repl_index, reason, exec_count, uptime_s)`, `log_repl_crash(agent_id, container, repl_index, reason)`, `log_pip_install_start(agent_id, container, role, packages)`, `log_pip_install_done(agent_id, container, role, duration_ms, packages_count)`, `log_pip_install_error(agent_id, container, role, package, error)`
5. Implement execution methods: `log_exec_start(agent_id, exec_id, code_length, has_await)`, `log_exec_complete(agent_id, exec_id, duration_ms, stdout_bytes, tool_calls, namespace_keys, namespace_size_kb)`, `log_exec_error(agent_id, exec_id, error, traceback_preview, duration_ms)`, `log_exec_timeout(agent_id, exec_id, timeout_seconds, tool_calls_before_timeout)`
6. Implement tool dispatch methods: `log_tool_call_start(exec_id, tool, params)`, `log_tool_call_complete(exec_id, tool, duration_ms, result_bytes, result_preview)`, `log_tool_call_error(exec_id, tool, error)`, `log_tool_call_denied(exec_id, tool, role, reason)`
7. Implement IPC methods: `log_ipc_connect(agent_id, socket_path)`, `log_ipc_disconnect(agent_id, reason, pending_calls)`, `log_ipc_ping(agent_id, latency_ms)`
8. Write tests: each event type emits correct JSONL, envelope has all required fields, empty strings omitted, file append-only, result_preview truncated to 200 chars

**Out of scope:**
- Log rotation (deferred — future work)
- TUI dashboard (deferred)
- Debug log code (config-driven, not logger responsibility)

**Invariants:**
- `inv-04-01` [existence]: PtcEventLogger importable — `python -c "from event_logger import PtcEventLogger"`
- `inv-04-02` [behavioral]: Emitted events are valid JSONL — test reads file, json.loads each line
- `inv-04-03` [behavioral]: All 20+ convenience methods exist — `python -c "from event_logger import PtcEventLogger; l = PtcEventLogger.__dict__; assert 'log_container_create' in l and 'log_exec_start' in l and 'log_tool_call_start' in l and 'log_repl_start' in l"`
- `inv-04-04` [behavioral]: Tests pass — `pytest tests/ptc/test_event_logger.py -v`

**Design doc reference:** Component 8 (lines 913-1046), event types (lines 932-973)

---

## chunk-05: IPC Host (Host-side Socket Server)

**Purpose:** core_logic
**Delivers:** Host-side server can accept a container connection via Unix socket, send execute commands, receive and dispatch tool_call messages concurrently, return tool_results, and receive complete/error responses.
**Dependencies:** chunk-01, chunk-04

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/ipc_host.py` (create)
- Tests: `tests/ptc/test_ipc_host.py` (create)

**Tasks:**
1. Implement `IpcHost.__init__(socket_path, tool_dispatcher, event_logger)` — store deps, init pending_futures dict
2. Implement `start()` — create Unix socket with `asyncio.start_unix_server`, set permissions 0o666
3. Implement `_handle_client(reader, writer)` — connection handler, store reader/writer
4. Implement `send_execute(exec_id, code, role, timeout) -> CompleteMsg | ErrorMsg`:
   - Send ExecuteMsg via socket
   - Read responses in loop (NDJSON line-by-line):
     - ToolCallMsg → dispatch via self._dispatcher (concurrent with semaphore), send ToolResultMsg back
     - CompleteMsg → resolve future, return
     - ErrorMsg → resolve future, return
   - On timeout → send CancelMsg, return timeout error
   - Log every event via event_logger
5. Handle parallel tool calls: multiple ToolCallMsg can arrive before any is resolved; dispatch all concurrently via asyncio.create_task, respond to each independently as they complete
6. Implement `health_check() -> bool` — send PingMsg, wait for PongMsg with timeout
7. Implement `stop()` — close writer, cancel server, remove socket file
8. Write tests with mock socket pairs: full execute→complete cycle, execute with tool_call→tool_result, parallel tool calls, timeout→cancel, health check

**Out of scope:**
- Docker container management (that's chunk-06)
- Actual tool execution (mock the dispatcher)
- Multiple container connections (one IpcHost per REPL)

**Invariants:**
- `inv-05-01` [existence]: IpcHost importable — `python -c "from ipc_host import IpcHost"`
- `inv-05-02` [behavioral]: Full execute→complete cycle works with mock — tested in test_ipc_host.py
- `inv-05-03` [behavioral]: Tests pass — `pytest tests/ptc/test_ipc_host.py -v`

**Design doc reference:** Component 2 (lines 349-387)

---

## chunk-06: Container Manager Core

**Purpose:** core_logic
**Delivers:** Docker containers can be created for agent roles with runtime pip install, a single REPL started per container, code executed via IPC, and containers stopped cleanly.
**Dependencies:** chunk-01, chunk-04, chunk-05

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/container_manager.py` (create)
- Tests: `tests/ptc/test_container_manager.py` (create)

**Tasks:**
1. Define `ReplHandle` dataclass: agent_id, repl_index, ipc (IpcHost), started_at, exec_count, tool_call_count
2. Define `ContainerInfo` dataclass: container_name, role, created_at, last_used_at, repls dict, max_repls, packages_installed
3. Implement `ContainerManager.__init__(config, project_root, registry, event_logger)` — init container tracking dicts, agent-to-container map, per-agent locks
4. Implement `_create_container(role) -> ContainerInfo`:
   - Build `docker run -d` command with: --name, --memory, --cpus, --network none, --tmpfs, -v mounts (project:ro, socket_dir, runtime:ro, ptc_tools:ro), -e PYTHONPATH, image, sleep infinity
   - Await docker exec + log container_create event
5. Implement `_install_role_packages(container_name, role)` — pip install from ROLE_PACKAGES dict via docker exec, with timeout, log pip_install events
6. Implement `_start_repl_in_container(container, agent_id) -> ReplHandle`:
   - Determine repl_index from len(container.repls)
   - docker exec -d for runtime.py --socket repl-{N}.sock
   - Create IpcHost for this REPL's socket
   - Wait for health_check (pong)
   - Register in container.repls and agent_to_container
   - Log repl_start event
7. Implement basic `get_or_create_repl(agent_id, role) -> ReplHandle` — check existing → create new container + REPL (sub-agent routing deferred to chunk-07)
8. Implement `execute_code(agent_id, code, timeout) -> dict` — acquire per-agent lock, delegate to repl.ipc.send_execute
9. Implement `stop_container(container_name)`, `stop_all()`, `health_check(agent_id)`, `reset_namespace(agent_id)`
10. Include `ROLE_PACKAGES` dict with all 6 roles
11. Write tests with Docker mocks: container creation command assembly, pip install invocation, REPL start, execute delegation, stop/cleanup

**Out of scope:**
- Sub-agent routing (chunk-07)
- Handoff logic (chunk-07)
- Idle container reuse (chunk-07)
- Error recovery / crash handling (chunk-13)

**Invariants:**
- `inv-06-01` [existence]: ContainerManager, ReplHandle, ContainerInfo importable — `python -c "from container_manager import ContainerManager, ReplHandle, ContainerInfo"`
- `inv-06-02` [behavioral]: ROLE_PACKAGES has all 6 roles — `python -c "from container_manager import ROLE_PACKAGES; assert set(ROLE_PACKAGES.keys()) == {'explorer','coder','auditor','tester','researcher','strategist'}"`
- `inv-06-03` [behavioral]: Container creation builds correct docker command — tested via mock
- `inv-06-04` [behavioral]: Tests pass — `pytest tests/ptc/test_container_manager.py -v`

**Design doc reference:** Component 6 (lines 537-667), ROLE_PACKAGES (lines 671-741), Docker config (lines 749-778)

---

## chunk-07: REPL Pool + Handoff

**Purpose:** extension
**Delivers:** Multiple REPL processes run independently in one container. Sub-agents share parent's container. Handoff preserves container but resets REPL. Idle containers reused by same-role agents.
**Dependencies:** chunk-06

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/container_manager.py` (modify)
- Tests: `tests/ptc/test_repl_pool.py` (create)

**Tasks:**
1. Extend `get_or_create_repl()` with full routing priority:
   - Existing REPL → return it
   - Sub-agent → start new REPL in parent's container (via _parent_map)
   - Idle container with same role → reuse it (new REPL)
   - Under max_containers → create new container + REPL
2. Implement `register_parent(sub_agent_id, parent_agent_id)` — populate _parent_map
3. Implement `handle_handoff(old_agent_id, new_agent_id, reset_namespace=True)`:
   - Find old agent's container
   - Kill old agent's REPL process (docker exec kill)
   - Remove old agent from container.repls
   - Start fresh REPL for new agent
   - Update agent_to_container mapping
   - Log container_handoff event
4. Implement `stop_repl(agent_id)` — kill specific REPL, remove from container.repls, container stays alive
5. Add idle container reuse: in get_or_create_repl, check for containers with matching role and empty repls dict
6. Enforce `max_repls_per_container`: check len(container.repls) before starting new REPL, return error if at limit
7. Write tests: sub-agent routing, REPL isolation (separate namespaces), handoff preserves container, idle reuse, max REPL enforcement, concurrent REPL execution

**Out of scope:**
- Idle timeout (chunk-13)
- REPL crash recovery (chunk-13)
- Container OOM handling (chunk-13)

**Invariants:**
- `inv-07-01` [behavioral]: Sub-agent routes to parent's container — tested via mock
- `inv-07-02` [behavioral]: Handoff preserves container name but starts fresh REPL — tested
- `inv-07-03` [constraint]: Max REPL enforcement returns error — tested
- `inv-07-04` [behavioral]: Tests pass — `pytest tests/ptc/test_repl_pool.py -v`

**Design doc reference:** Component 6 — REPL pool routing (lines 578-610), handoff (lines 640-652), lifecycle (lines 780-832)

**Verification criteria covered:** 15 (REPL start <500ms), 16 (multiple REPLs), 17 (REPL isolation), 18 (sub-agent routing), 19 (REPL cleanup), 20 (max REPLs), 21 (handoff preserves), 22 (handoff resets), 23 (container reuse)

---

## chunk-08: ptc_tools Library — Direct-Call Functions

**Purpose:** core_logic
**Delivers:** 7 tool functions (read_file, write_file, list_dir, search_code, analyze_imports, analyze_structure, count_tokens) extracted from old fallback() code, callable on host returning structured dicts. Base utility with project root detection.
**Dependencies:** none

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/ptc_tools/files.py` (create)
- Also: `~/.claude/mcp/ptc-server/ptc_tools/__init__.py` (create), `ptc_tools/_base.py` (create), `ptc_tools/search.py` (create), `ptc_tools/tokens.py` (create)
- Tests: `tests/ptc/test_ptc_tools.py` (create)

**Tasks:**
1. Create `ptc_tools/` package with `__init__.py` — convenience re-exports of all public functions
2. Implement `_base.py`: `get_project_root()` detects `/workspace` (container) vs host path, define `MAX_FILE_SIZE`, `DEFAULT_ENCODING` constants
3. Extract `read_file(path, project_root, encoding='utf-8') -> dict` from `tool_implementations/read_file.py` fallback() → `ptc_tools/files.py`. Returns `{path, lines, size_bytes, content, truncated}`
4. Extract `write_file(path, content, project_root, encoding='utf-8') -> dict` → `ptc_tools/files.py`. Returns `{path, size_bytes, written}`
5. Extract `list_dir(path, project_root, max_depth=3, include_hidden=False) -> dict` → `ptc_tools/files.py`
6. Extract `search_code(pattern, project_root, glob_pattern='**/*.py', max_results=50) -> dict` → `ptc_tools/search.py`. Returns `{pattern, files_searched, matches, total_matches, truncated}`
7. Extract `analyze_imports(glob_pattern, project_root, depth=2) -> dict` → `ptc_tools/search.py`
8. Extract `analyze_structure(glob_pattern, project_root, include_metrics=False) -> dict` → `ptc_tools/search.py`
9. Extract `count_tokens(paths, project_root) -> dict` → `ptc_tools/tokens.py`
10. Write tests for each function: correct return dict structure, file not found handling, project root detection

**Out of scope:**
- Subprocess-based tools (chunk-09)
- Host-network tools (chunk-09)
- Tool dispatcher routing (chunk-10)
- Removing old tool_implementations/ (chunk-13)

**Invariants:**
- `inv-08-01` [existence]: All functions importable — `python -c "from ptc_tools.files import read_file, write_file, list_dir; from ptc_tools.search import search_code, analyze_imports, analyze_structure; from ptc_tools.tokens import count_tokens"`
- `inv-08-02` [behavioral]: read_file returns dict with expected keys — tested
- `inv-08-03` [behavioral]: get_project_root() returns valid path — `python -c "from ptc_tools._base import get_project_root; p = get_project_root(); assert p"`
- `inv-08-04` [behavioral]: Tests pass — `pytest tests/ptc/test_ptc_tools.py -v -k 'files or search or tokens'`

**Design doc reference:** Component 4 (lines 465-486), tool signatures from ptc-context.json tools_15 section

---

## chunk-09: ptc_tools — Subprocess + Host Functions

**Purpose:** core_logic
**Delivers:** All remaining tool functions (run_tests, run_linter, coverage_summary, git tools, network tools) extracted and callable. Complete ptc_tools library with all 15 functions.
**Dependencies:** chunk-08

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/ptc_tools/testing.py` (create)
- Also: `ptc_tools/git.py` (create), `ptc_tools/network.py` (create)
- Tests: `tests/ptc/test_ptc_tools.py` (extend)

**Tasks:**
1. Extract `run_tests(test_path, project_root, markers='', verbose=False) -> dict` → `ptc_tools/testing.py`. Returns `{passed, failed, errors, failures, duration_seconds}`
2. Extract `run_linter(paths, project_root, fix=False) -> dict` → `ptc_tools/testing.py`
3. Extract `coverage_summary(test_path, project_root, source_dirs='') -> dict` → `ptc_tools/testing.py`
4. Extract `git_diff_summary(base_branch='main', project_root='.') -> dict` → `ptc_tools/git.py`. Returns `{base_branch, files_changed, insertions, deletions, changes}`
5. Extract `diff_summary(base_ref, project_root, head_ref='HEAD') -> dict` → `ptc_tools/git.py`
6. Extract `list_changes(since_ref='HEAD~1', project_root='.') -> dict` → `ptc_tools/git.py`
7. Extract `web_fetch_summary(url, project_root, prompt='') -> dict` → `ptc_tools/network.py` (host-only)
8. Extract `mcp_call_summary(server_name, tool_name, project_root, arguments='') -> dict` → `ptc_tools/network.py` (v1 stub)
9. Update `ptc_tools/__init__.py` with new re-exports
10. Write tests: subprocess functions with mock subprocess, git functions with temp git repo, network stubs return correct structure

**Out of scope:**
- Actual pytest/ruff execution in tests (mock subprocess)
- MCP proxy implementation (v1 stub only)
- Removing old tool_implementations/ (chunk-13)

**Invariants:**
- `inv-09-01` [existence]: All functions importable — `python -c "from ptc_tools.testing import run_tests, run_linter, coverage_summary; from ptc_tools.git import git_diff_summary, diff_summary, list_changes; from ptc_tools.network import web_fetch_summary, mcp_call_summary"`
- `inv-09-02` [behavioral]: run_tests returns dict with pass/fail keys — tested
- `inv-09-03` [behavioral]: Tests pass — `pytest tests/ptc/test_ptc_tools.py -v -k 'testing or git or network'`

**Design doc reference:** Component 4 (lines 465-486), strategy mapping (lines 527-533)

---

## chunk-10: Tool Dispatcher

**Purpose:** core_logic
**Delivers:** Tool calls from IPC can be routed to the correct execution strategy (ptc_tools, subprocess, host_fs, host_network) with role permission checking and semaphore backpressure.
**Dependencies:** chunk-04, chunk-08, chunk-09

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/tool_dispatcher.py` (create)
- Tests: `tests/ptc/test_tool_dispatcher.py` (create)

**Tasks:**
1. Implement `ToolDispatcher.__init__(registry, project_root, event_logger, max_concurrent=4)` — store deps, create `asyncio.Semaphore(max_concurrent)`
2. Implement `dispatch(role, tool_name, input, exec_id) -> str` (JSON result):
   - Check `registry.is_allowed(role, tool_name)` — return error JSON if denied
   - Acquire semaphore
   - Get strategy from `registry.get_strategy(tool_name)`
   - Route to appropriate dispatch function
   - Log start/complete/error events
   - Return `json.dumps(result)`
3. Implement `ptc_tools_dispatch(tool_name, input, project_root)` — import and call ptc_tools function by name
4. Implement `subprocess_dispatch(tool_name, input, project_root)` — async call to subprocess-based tools (run_tests, git, etc.)
5. Implement `host_fs_dispatch(tool_name, input, project_root)` — write_file on host
6. Implement `host_network_dispatch(tool_name, input, project_root)` — web_fetch_summary, mcp_call_summary
7. Write tests: strategy routing (each tool maps to correct strategy), permission denial returns error, semaphore limits concurrent dispatch to 4, event logging occurs

**Out of scope:**
- MCP proxy strategy (stub only)
- Integration with IPC Host (already wired in chunk-05 via dependency injection)

**Invariants:**
- `inv-10-01` [existence]: ToolDispatcher importable — `python -c "from tool_dispatcher import ToolDispatcher"`
- `inv-10-02` [behavioral]: Correct strategy routing — tested for each of the 15 tools
- `inv-10-03` [constraint]: Permission denial returns JSON error — tested
- `inv-10-04` [constraint]: Semaphore limits concurrency to 4 — tested
- `inv-10-05` [behavioral]: Tests pass — `pytest tests/ptc/test_tool_dispatcher.py -v`

**Design doc reference:** Component 5 (lines 489-533)

---

## chunk-11: Docker Image + Config + Registry Updates

**Purpose:** integration
**Delivers:** Base Docker image builds and passes verification. Config.json has all new sections (role_tools, tool_strategies, repl_pool, ipc, observability). Tool registry supports execute permission and strategy lookup.
**Dependencies:** chunk-08, chunk-09

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/Dockerfile` (create)
- Also: `~/.claude/mcp/ptc-server/build-image.sh` (create), `~/.claude/mcp/ptc-server/config.json` (rewrite), `~/.claude/mcp/ptc-server/tool_registry.py` (modify)
- Tests: `tests/ptc/test_tool_registry.py` (modify — add strategy tests)

**Tasks:**
1. Write `Dockerfile`: FROM python:3.11-slim, install git, pip install pyyaml toml pytest ruff, create non-root ptcuser, WORKDIR /workspace, no entrypoint
2. Write `build-image.sh`: docker build -t ptc-sandbox:latest, verify base image (check python, yaml, pytest, ruff imports), print image size
3. Rewrite `config.json` with complete new schema:
   - `role_tools`: 6 roles with "execute" + role-specific tools
   - `tool_strategies`: 15 tool→strategy mappings
   - `resource_limits`: memory_mb, cpu_cores, timeout_seconds, max_containers, disk_mb, max_output_bytes, idle_timeout_seconds, max_tool_calls_per_execution
   - `repl_pool`: max_repls_per_container, repl_start_timeout_seconds, repl_idle_timeout_seconds, pip_install_timeout_seconds
   - `docker`: image, network_mode, container_prefix
   - `ipc`: socket_dir, socket_permissions, connect_timeout_seconds, tool_call_timeout_seconds
   - `observability`: log_file, debug_log_code, result_preview_chars
4. Modify `tool_registry.py`:
   - Add "execute" to role_tools
   - Add `get_strategy(tool_name) -> str` method using tool_strategies from config
   - Load tool_strategies from config on init
5. Write/update tests: registry allows "execute" for all roles, get_strategy returns correct strategy for each tool, config loads all sections

**Out of scope:**
- Actually running Docker build in CI (manual verification)
- Per-role Dockerfiles (deferred)

**Invariants:**
- `inv-11-01` [existence]: Dockerfile exists with correct base image — `grep 'FROM python:3.11-slim' ~/.claude/mcp/ptc-server/Dockerfile`
- `inv-11-02` [behavioral]: Config loads all required sections — `python -c "import json; c = json.load(open('config.json')); assert all(k in c for k in ['role_tools','tool_strategies','resource_limits','repl_pool','docker','ipc','observability'])"`
- `inv-11-03` [behavioral]: Registry supports execute and get_strategy — `python -c "from tool_registry import ToolRegistry; # tested in test_tool_registry.py"`
- `inv-11-04` [system]: Tests pass — `pytest tests/ptc/test_tool_registry.py -v`

**Design doc reference:** Component 9 (lines 1050-1264) for Dockerfile/build-image, Component 10 (lines 1268-1330) for config

---

## chunk-12: MCP Server Rewrite

**Purpose:** integration
**Delivers:** Complete MCP server with 5 tools (ptc_execute, ptc_status, ptc_reset_namespace, ptc_shutdown, ptc_shutdown_all) wired to container manager, tool dispatcher, and event logger. End-to-end ptc_execute call works.
**Dependencies:** chunk-06, chunk-07, chunk-10, chunk-11

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/server.py` (rewrite)
- Also: `~/.claude/mcp/ptc-server/requirements.txt` (update)
- Tests: `tests/ptc/test_ptc_execute.py` (create)

**Tasks:**
1. Rewrite `server.py`: remove ALL 15 old @mcp.tool() handlers
2. Initialize FastMCP("ptc-sandbox"), load config, create event_logger, registry, tool_dispatcher, container_manager
3. Implement `ptc_execute(agent_id, role, code, timeout=0) -> str`:
   - Check Docker availability
   - Check registry.is_allowed(role, "execute")
   - Resolve effective timeout from config
   - get_or_create_repl → execute_code → cap output → return
4. Implement `ptc_status(agent_id='') -> str` — health check or all status
5. Implement `ptc_reset_namespace(agent_id) -> str`
6. Implement `ptc_shutdown(agent_id) -> str`
7. Implement `ptc_shutdown_all() -> str`
8. Implement `_cap_output(output, max_bytes) -> str` — truncate with flag at 64KB
9. Update `requirements.txt` (mcp[cli] should already be there)
10. Write end-to-end tests with mocked Docker: ptc_execute returns stdout, ptc_status returns health, ptc_shutdown cleans up, output cap enforced, invalid role rejected

**Out of scope:**
- Real Docker integration (tests use mocks)
- Error recovery details (chunk-13)

**Invariants:**
- `inv-12-01` [existence]: server.py imports without error — `python -c "import server"` (from ptc-server dir)
- `inv-12-02` [behavioral]: ptc_execute registered as MCP tool — `python -c "from server import mcp; assert 'ptc_execute' in [t.name for t in mcp._tools]"` (or equivalent check)
- `inv-12-03` [constraint]: Output cap enforced at 64KB — tested
- `inv-12-04` [system]: Tests pass — `pytest tests/ptc/test_ptc_execute.py -v`

**Design doc reference:** Component 7 (lines 836-909)

**Verification criteria covered:** 1 (IPC round-trip), 6 (role enforcement), 8 (output cap), 11 (eager startup), 14 (Docker unavailable)

---

## chunk-13: Error Handling + Hardening + Cleanup

**Purpose:** hardening
**Delivers:** All error scenarios handled robustly: crash recovery, timeouts, namespace eviction, REPL limits, pip failures, idle cleanup. Old code (make_kernel_code, old tests) removed.
**Dependencies:** chunk-12

**Scope:**
- Primary: `~/.claude/mcp/ptc-server/container_manager.py` (modify)
- Also: `~/.claude/mcp/ptc-server/ipc_host.py` (modify), `sandbox_runtime/runtime.py` (modify), `server.py` (modify)
- Tests: additions across `test_container_manager.py`, `test_ipc_host.py`, `test_ptc_execute.py`

**Tasks:**
1. **Container crash during execution:** IpcHost detects socket EOF → reject all pending futures with ContainerCrashError → return error (NEVER re-execute)
2. **Container crash during idle:** ContainerManager detects ping timeout on next call → restart container → namespace lost → return warning
3. **REPL crash:** Socket EOF on specific REPL → kill only that REPL → container + other REPLs survive → affected agent gets error
4. **Per-tool timeout:** asyncio.wait_for on each tool dispatch → send ToolError result to container on timeout
5. **Overall execution timeout:** Deadline timer in IpcHost.send_execute → send CancelMsg → interrupt code → return timeout error
6. **Max tool calls per execution:** Counter in IpcHost → send cancel after limit → return partial result
7. **Namespace eviction at 512MB:** Executor evicts oldest non-callable objects when namespace exceeds 512MB
8. **Pip install timeout:** asyncio.wait_for on pip install → log error → container created but unusable for role-specific packages
9. **Pip install failure:** Non-zero exit → log with package name → container usable for stdlib only
10. **Docker unavailability:** Check `docker info` at server startup → set flag → ptc_execute returns clear error
11. **Idle timeout:** Background task in ContainerManager → destroy containers with no active REPLs after idle_timeout_seconds
12. **Remove `make_kernel_code()` from all 15 files in `tool_implementations/`** — keep fallback() for reference
13. **Delete old test files:** `test_kernel_manager.py`, `test_server.py` (old), `test_tools.py` (old)
14. Write tests for each error scenario: crash returns error, timeout sends cancel, namespace eviction works, permission denial logged

**Out of scope:**
- Deleting tool_implementations/ entirely (keep fallback() for reference until ptc_tools validated)
- Log rotation

**Invariants:**
- `inv-13-01` [behavioral]: Crash during execution returns error, never re-executes — tested
- `inv-13-02` [behavioral]: Timeout sends CancelMsg — tested
- `inv-13-03` [constraint]: Namespace eviction at 512MB — tested
- `inv-13-04` [system]: No make_kernel_code in tool_implementations — `grep -r "make_kernel_code" ~/.claude/mcp/ptc-server/tool_implementations/ | wc -l` returns 0
- `inv-13-05` [system]: All error scenarios return structured JSON error — tested
- `inv-13-06` [system]: Tests pass — `pytest tests/ptc/ -v`

**Design doc reference:** Error Handling table (lines 1334-1354), crash recovery (lines 1355-1372)

**Verification criteria covered:** 2 (parallel tools), 3 (backpressure), 5 (namespace eviction), 7 (timeout), 24 (idle timeout), 25 (crash during exec), 26 (crash during idle), 27 (REPL crash)

---

## chunk-14: PTC Skills Documentation

**Purpose:** extension (documentation — no Python code)
**Delivers:** Complete PTC skill documentation: universal mechanics, 6 role-specific specs with packages and recipes, reference docs for patterns and anti-patterns, and robotics-cv domain specialization.
**Dependencies:** none (can be done at any point; best after chunk-12 when full system is understood)

**Scope:**
- Primary: `skills/ptc-sandbox/SKILL.md` (create)
- Also: `skills/ptc-sandbox/role-specs/explorer.md` (create), `coder.md`, `auditor.md`, `tester.md`, `researcher.md`, `strategist.md`, `skills/ptc-sandbox/references/tool-calling-patterns.md` (create), `anti-patterns.md` (create), `skills/ptc-sandbox/specializations/robotics-cv.md` (create)
- Note: All paths relative to `/home/kartik/personal/agentic_workflow/`

**Tasks:**
1. Write `SKILL.md` — universal PTC mechanics (~900 tokens): ptc_execute API, available tools, print discipline, parallel tool calls, state persistence, error handling, anti-patterns summary
2. Write `role-specs/explorer.md` — packages (16), when to use (codebase analysis, surgical query, git history), when not to use, self-serve vs delegation
3. Write `role-specs/coder.md` — packages (12), when to use (pre-implementation, resource efficiency, scope verification, quality gate batching), when not to use
4. Write `role-specs/auditor.md` — packages (13), when to use (task audit, phase audit, hardening, arbitration), mandatory PTC checks
5. Write `role-specs/tester.md` — packages (9), when to use (property-based, coverage gaps, flakiness, performance, memory), blind wall constraint
6. Write `role-specs/researcher.md` — packages (10), when to use (clean web, deduplicate, parse PDFs, compare), when not
7. Write `role-specs/strategist.md` — packages (4), when to use (plan validation only)
8. Write `references/tool-calling-patterns.md` — await, asyncio.gather, error handling, loops, batching
9. Write `references/anti-patterns.md` — printing raw data, repeated single calls, ignoring packages, PTC for simple reads
10. Write `specializations/robotics-cv.md` — domain packages (opencv, torch, scipy), image analysis patterns
11. Add PTC delegation section to sub-agent-delegation skill — document sub-agent container sharing, REPL pool routing, namespace isolation

**Out of scope:**
- data-engineering.md domain spec (only robotics-cv for now)

**Invariants:**
- `inv-14-01` [existence]: SKILL.md exists with correct YAML frontmatter — `grep 'name: ptc-sandbox' skills/ptc-sandbox/SKILL.md`
- `inv-14-02` [existence]: All 6 role-spec files exist — `ls skills/ptc-sandbox/role-specs/*.md | wc -l` returns 6
- `inv-14-03` [existence]: Reference docs exist — `ls skills/ptc-sandbox/references/*.md | wc -l` returns 2

**Design doc reference:** PTC Skill Design section (lines 1513-1903)

---

## Verification Criteria Mapping

The design doc defines 30 verification criteria. Here is which chunk covers each:

| # | Criterion | Primary Chunk | Verification |
|---|-----------|--------------|--------------|
| 1 | IPC round-trip | chunk-05 + chunk-03 | test_ipc_host.py |
| 2 | Parallel tools | chunk-05 + chunk-13 | test_ipc_host.py |
| 3 | Backpressure (semaphore 4) | chunk-10 + chunk-13 | test_tool_dispatcher.py |
| 4 | Persistent state | chunk-02 | test_sandbox_runtime.py |
| 5 | Namespace eviction 512MB | chunk-13 | test_sandbox_runtime.py |
| 6 | Role enforcement | chunk-10 + chunk-12 | test_tool_dispatcher.py |
| 7 | Timeout cancellation | chunk-13 | test_ipc_host.py |
| 8 | Output cap 64KB | chunk-12 | test_ptc_execute.py |
| 9 | Base image build | chunk-11 | build-image.sh |
| 10 | Runtime pip install | chunk-06 | test_container_manager.py |
| 11 | Eager startup | chunk-12 | test_ptc_execute.py |
| 12 | Package availability | chunk-11 | build-image.sh verify |
| 13 | ptc_tools importable | chunk-08 | test_ptc_tools.py |
| 14 | Docker unavailable | chunk-12 + chunk-13 | test_ptc_execute.py |
| 15 | REPL start <500ms | chunk-07 | test_repl_pool.py |
| 16 | Multiple REPLs independent | chunk-07 | test_repl_pool.py |
| 17 | REPL isolation | chunk-07 | test_repl_pool.py |
| 18 | Sub-agent routing | chunk-07 | test_repl_pool.py |
| 19 | REPL cleanup | chunk-07 | test_repl_pool.py |
| 20 | Max REPLs enforcement | chunk-07 | test_repl_pool.py |
| 21 | Handoff preserves container | chunk-07 | test_repl_pool.py |
| 22 | Handoff resets namespace | chunk-07 | test_repl_pool.py |
| 23 | Container reuse | chunk-07 | test_repl_pool.py |
| 24 | Idle timeout | chunk-13 | test_container_manager.py |
| 25 | Crash during execution | chunk-13 | test_ipc_host.py |
| 26 | Crash during idle | chunk-13 | test_container_manager.py |
| 27 | REPL crash isolation | chunk-13 | test_repl_pool.py |
| 28 | All events in JSONL | chunk-04 | test_event_logger.py |
| 29 | REPL events logged | chunk-04 | test_event_logger.py |
| 30 | Queryable with jq | chunk-04 | test_event_logger.py |

---

## Quality Gates

```bash
# Per-chunk quick checks (run by chunk-coder):
ruff format .
ruff check . --fix && ruff check .
pyright
pytest tests/ptc/ -v

# Full gate (run before commit):
./scripts/gate.sh
```

---

## File Inventory

### New Files (ptc-server)
| File | Chunk | Purpose |
|------|-------|---------|
| `ipc_protocol.py` | 01 | Message types + NDJSON |
| `sandbox_runtime/__init__.py` | 02 | Package init |
| `sandbox_runtime/executor.py` | 02 | Code execution + namespace |
| `sandbox_runtime/runtime.py` | 03 | Container entry point |
| `ipc_sandbox.py` | 03 | Container-side IPC client |
| `event_logger.py` | 04 | JSONL observability |
| `ipc_host.py` | 05 | Host-side socket server |
| `container_manager.py` | 06+07 | Docker + REPL pool |
| `ptc_tools/__init__.py` | 08 | Re-exports |
| `ptc_tools/_base.py` | 08 | Project root + constants |
| `ptc_tools/files.py` | 08 | read_file, write_file, list_dir |
| `ptc_tools/search.py` | 08 | search_code, analyze_imports, analyze_structure |
| `ptc_tools/tokens.py` | 08 | count_tokens |
| `ptc_tools/testing.py` | 09 | run_tests, run_linter, coverage_summary |
| `ptc_tools/git.py` | 09 | git_diff_summary, diff_summary, list_changes |
| `ptc_tools/network.py` | 09 | web_fetch_summary, mcp_call_summary |
| `tool_dispatcher.py` | 10 | Strategy routing |
| `Dockerfile` | 11 | Base image |
| `build-image.sh` | 11 | Build + verify script |

### Modified Files (ptc-server)
| File | Chunk | Change |
|------|-------|--------|
| `server.py` | 12 | Complete rewrite (15 tools → 5) |
| `config.json` | 11 | Complete rewrite (new schema) |
| `tool_registry.py` | 11 | Add execute, get_strategy() |
| `requirements.txt` | 12 | Verify/update |

### New Test Files (workflow repo)
| File | Chunk | Tests |
|------|-------|-------|
| `tests/ptc/conftest.py` | 01 | Shared fixtures (create/rewrite) |
| `tests/ptc/test_ipc_protocol.py` | 01 | Message types, encode/decode |
| `tests/ptc/test_sandbox_runtime.py` | 02+03 | Executor, IPC client, runtime |
| `tests/ptc/test_event_logger.py` | 04 | All event types |
| `tests/ptc/test_ipc_host.py` | 05 | Socket server, tool dispatch loop |
| `tests/ptc/test_container_manager.py` | 06 | Container lifecycle |
| `tests/ptc/test_repl_pool.py` | 07 | Multi-REPL, sub-agents, handoff |
| `tests/ptc/test_ptc_tools.py` | 08+09 | All 15 tool functions |
| `tests/ptc/test_tool_dispatcher.py` | 10 | Strategy routing, permissions |
| `tests/ptc/test_ptc_execute.py` | 12 | End-to-end MCP tool tests |

### New Skill Files (workflow repo)
| File | Chunk |
|------|-------|
| `skills/ptc-sandbox/SKILL.md` | 14 |
| `skills/ptc-sandbox/role-specs/explorer.md` | 14 |
| `skills/ptc-sandbox/role-specs/coder.md` | 14 |
| `skills/ptc-sandbox/role-specs/auditor.md` | 14 |
| `skills/ptc-sandbox/role-specs/tester.md` | 14 |
| `skills/ptc-sandbox/role-specs/researcher.md` | 14 |
| `skills/ptc-sandbox/role-specs/strategist.md` | 14 |
| `skills/ptc-sandbox/references/tool-calling-patterns.md` | 14 |
| `skills/ptc-sandbox/references/anti-patterns.md` | 14 |
| `skills/ptc-sandbox/specializations/robotics-cv.md` | 14 |

### Deleted Files (chunk-13)
| File | Reason |
|------|--------|
| `tests/ptc/test_kernel_manager.py` | Old tests for replaced kernel_manager |
| `tests/ptc/test_server.py` | Old tests for 15-tool server |
| `tests/ptc/test_tools.py` | Old tests for tool_implementations |
