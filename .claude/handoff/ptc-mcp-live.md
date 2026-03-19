# PTC MCP Server — Live Handoff

## Status

The PTC (Programmatic Tool Calling) system is **fully functional and registered as an MCP server**. All 97 integration tests pass. The MCP server `local-ptc` is registered and will be available in new Claude Code sessions.

## What Was Done

A previous session fixed 6 source code bugs + 6 test issues + 1 architectural deadlock in the PTC system. Key changes:

1. **executor.py** — `execute()` made async, replaced `asyncio.run()` with `await`
2. **runtime.py** — Restructured with `asyncio.create_task` to prevent deadlock; added `--tools` CLI arg
3. **container_manager.py** — Fixed handoff logger signature, removed `wily`, passes role tools to REPL, removed `/ptc_tools_lib` from PYTHONPATH
4. **ipc_host.py** — 1MB stream buffer, `exec_error` logging, fixed dispatcher call arg order
5. **server.py** — Fixed wiring bug: passes `tool_dispatcher` (not `ToolRegistry`) to ContainerManager
6. **MCP registered:** `claude mcp add local-ptc -- python3 ~/.claude/mcp/ptc-server/server.py`

## Step 1: Read These Files to Understand PTC

Read ALL of these before answering questions or making changes.

### Core Architecture (host side)
```
~/.claude/mcp/ptc-server/server.py              — MCP entry point, exposes ptc_execute/ptc_status/ptc_reset_namespace/ptc_shutdown tools
~/.claude/mcp/ptc-server/container_manager.py    — Docker container lifecycle, REPL pool, sub-agent routing, handoffs
~/.claude/mcp/ptc-server/ipc_host.py             — Host-side Unix socket server per REPL, handles execute→tool_call→tool_result→complete lifecycle
~/.claude/mcp/ptc-server/tool_dispatcher.py      — Routes tool calls to execution strategies (ptc_tools, subprocess, host_fs, host_network)
~/.claude/mcp/ptc-server/tool_registry.py        — Role-scoped tool permissions
~/.claude/mcp/ptc-server/event_logger.py         — JSONL observability for all events
~/.claude/mcp/ptc-server/config.json             — All configuration (roles, tools, limits, docker, ipc)
```

### IPC Protocol & Container Runtime (runs inside Docker)
```
~/.claude/mcp/ptc-server/ipc_protocol.py              — NDJSON message types (ExecuteMsg, ToolCallMsg, CompleteMsg, etc.)
~/.claude/mcp/ptc-server/ipc_sandbox.py               — Container-side IPC client, sends tool_call, awaits tool_result
~/.claude/mcp/ptc-server/sandbox_runtime/runtime.py   — Container entry point, message loop with create_task for execution
~/.claude/mcp/ptc-server/sandbox_runtime/executor.py  — Code execution engine with persistent namespace, async tool stubs
```

### PTC Tools (host-side tool implementations)
```
~/.claude/mcp/ptc-server/ptc_tools/             — Tool functions: read_file, list_dir, search_code, etc.
```

### Design Document
```
~/personal/agentic_workflow/.claude/designs/ptc.md  — Full design doc (long, ~90KB, but comprehensive)
```

### Tests
```
~/personal/agentic_workflow/tests/ptc/integration/        — 97 integration tests across 5 phases
~/personal/agentic_workflow/tests/ptc/integration/conftest.py — Test fixtures and helpers
```

## Step 2: Test the MCP Server

The `local-ptc` MCP server is registered. In this session you should have access to these MCP tools:

- `ptc_execute(agent_id, role, code, timeout)` — Execute Python code in sandboxed container
- `ptc_status(agent_id)` — Get container/REPL status
- `ptc_reset_namespace(agent_id)` — Clear persistent namespace
- `ptc_shutdown(agent_id)` — Stop a specific REPL
- `ptc_shutdown_all()` — Stop all containers

### Quick smoke test sequence:

**Test 1: Basic execution**
```
ptc_execute(agent_id="test-1", role="explorer", code="print('hello from PTC')")
```

**Test 2: Namespace persistence**
```
ptc_execute(agent_id="test-1", role="explorer", code="x = 42")
ptc_execute(agent_id="test-1", role="explorer", code="print(x)")
```
Should print `42` — same agent_id, same namespace.

**Test 3: Tool call via IPC**
```
ptc_execute(agent_id="test-1", role="explorer", code="data = await read_file(path='schemas/handoff.py')\nprint(f'Read {len(data)} chars')")
```
This exercises the full pause-resume IPC pipeline.

**Test 4: Parallel tool calls**
```
ptc_execute(agent_id="test-1", role="explorer", code="import asyncio\nr = await asyncio.gather(read_file(path='schemas/handoff.py'), list_dir(path='.'))\nprint(f'file={len(r[0])} chars, dir={len(r[1])} entries')")
```

**Test 5: Cleanup**
```
ptc_shutdown_all()
```

### If the MCP server is NOT available

If the MCP tools don't appear (server failed to start), debug with:
```bash
# Check registration
claude mcp list

# Test the server directly
python3 ~/.claude/mcp/ptc-server/server.py 2>&1

# Check for import errors
python3 -c "import sys; sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude/mcp/ptc-server')); from server import *" 2>&1
```

You can also test programmatically without the MCP server (this always works):
```bash
cd ~/personal/agentic_workflow && python3 -c "
import asyncio, sys, json
sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude/mcp/ptc-server'))
from container_manager import ContainerManager
from event_logger import PtcEventLogger
from tool_dispatcher import ToolDispatcher
from tool_registry import ToolRegistry

async def main():
    config = json.load(open(str(__import__('pathlib').Path.home() / '.claude/mcp/ptc-server/config.json')))
    logger = PtcEventLogger(log_path=__import__('pathlib').Path('/tmp/ptc-test.jsonl'))
    registry = ToolRegistry(config)
    dispatcher = ToolDispatcher(registry=registry, project_root='$(pwd)', event_logger=logger)
    mgr = ContainerManager(config=config, project_root='$(pwd)', registry=dispatcher, event_logger=logger)
    mgr.docker_available = True
    await mgr.get_or_create_repl('test', 'explorer')
    r = await mgr.execute_code('test', 'print(\"PTC works\")', 30)
    print(r)
    await mgr.stop_all()

asyncio.run(main())
"
```

## Architecture Summary

```
Agent calls ptc_execute(agent_id, role, code)
  │
  └─→ server.py (MCP server, host process)
        │
        ├─→ ContainerManager.get_or_create_repl()
        │     └─→ Docker container with REPL process
        │
        └─→ ContainerManager.execute_code()
              └─→ IpcHost.send_execute() ──[Unix socket]──→ runtime.py
                    │                                          │
                    │  ←── tool_call (read_file, etc.) ────────┤
                    │                                          │ (execution PAUSES)
                    ├─→ ToolDispatcher.dispatch()               │
                    │     └─→ ptc_tools function                │
                    │  ──→ tool_result ─────────────────────────┤
                    │                                          │ (execution RESUMES)
                    │  ←── complete (stdout, namespace) ───────┘
                    │
                    └─→ Return stdout to agent (capped at 64KB)
```

Each `await tool_call()` in the agent's code triggers the pause-resume cycle. Multiple `await`s in one code block = multiple IPC round-trips. `asyncio.gather()` dispatches tools concurrently.

## What's Left To Do

1. **Write agent PTC skills** — Agents need instructions on WHEN and HOW to use PTC. Skill stubs exist at `~/personal/agentic_workflow/skills/ptc-sandbox/` but have no content.
2. **Namespace inspector** (nice-to-have) — Enrich `namespace_keys` return with types/sizes, or add a `ptc_inspect` tool.
3. **Eager container startup** (nice-to-have) — Start containers at agent launch instead of first PTC call.

## Key Design Facts for Q&A

- **Containers:** Docker, `python:3.11-slim`, one per role, persistent, max 8
- **REPL pool:** Up to 6 REPLs per container, sub-agents share parent's container
- **Namespace:** Persistent dict across calls, warn at 256MB, evict at 512MB
- **Tool stubs:** Async functions injected into namespace (`await read_file(...)`)
- **IPC:** Unix domain sockets, NDJSON wire format, 1MB message limit
- **Network:** Disconnected after pip install (full isolation)
- **Project mount:** `/workspace` read-only
- **Output cap:** 64KB at server.py layer, 1MB at IPC layer
- **Available tools per role:** Defined in config.json `role_tools`
- **Explorer tools:** execute, read_file, list_dir, search_code, analyze_imports, count_tokens
