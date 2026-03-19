# Handoff: PTC V2 Simplification

**Date**: 2026-03-07
**Previous session**: Deep analysis of PTC MCP architecture, comparison with Alibaba OpenSandbox, decision to simplify
**Status**: Plan approved, ready for implementation

---

## What Happened This Session

1. **Explored the entire PTC MCP codebase** — every file in `~/.claude/mcp/ptc-server/` was read and analyzed. Full architecture breakdown produced.

2. **Researched Alibaba OpenSandbox** — their new open-source agent sandbox (Apache 2.0, 6,400+ GitHub stars). Conclusion: OpenSandbox is a container runtime, PTC is a programming model. They solve different problems. OpenSandbox doesn't have PTC's core innovations (pause-resume IPC, context window optimization, REPL pooling).

3. **Decided to enable bridge networking** (`--network=bridge` instead of `--network=none`). This gives containers direct network access and makes 11 of 15 internal IPC tools redundant (they existed only because the container couldn't access the network or write to the filesystem).

4. **Decided to remove the IPC tool dispatch layer** entirely. With bridge networking + `/workspace:rw`, container code can do everything directly (file I/O, web requests, git operations, code analysis). The only operations that needed host-side dispatch were `run_tests` and `run_linter` (project dependencies not in container), and agents can use Claude Code's built-in Bash tool for those.

5. **Decided to keep the simplified IPC** for code submission. Persistent namespace requires a long-running REPL process, which requires some way to send code and receive stdout. The IPC stays but shrinks from 8 message types to 5 (just execute/complete/error/ping/pong — no tool dispatch).

6. **Incorporated hardening upgrades**: Pre-built role images (eliminate pip install), TTL-based container cleanup, REPL readiness polling (replace sleep), namespace eviction bug fix.

7. **Wrote `PTC-MCP-Hardening.md`** in project root — documents 4 targeted upgrades inspired by OpenSandbox analysis.

---

## The Plan

**Read this file**: `~/.claude/plans/ptc-v2-simplification.md`

It contains the complete implementation plan with 8 phases, exact code changes, files to delete, files to modify, and a verification checklist. Every file path and line number is referenced.

### Quick Summary of Phases

| Phase | What | Key Changes |
|-------|------|-------------|
| 1 | Delete tool dispatch layer | Delete `tool_dispatcher.py`, `tool_registry.py`, `ipc_sandbox.py`, entire `ptc_tools/` directory |
| 2 | Simplify IPC protocol | Remove ToolCallMsg, ToolResultMsg, CancelMsg. Simplify ipc_host.py and runtime.py |
| 3 | Enable bridge networking + RW mount | Change `--network=none` to `bridge`, `/workspace:ro` to `:rw`, delete `_isolate_network()`, `_install_role_packages()`, `ROLE_PACKAGES` |
| 4 | Simplify config and server | Strip config.json to essentials, remove dispatcher/registry from server.py |
| 5 | Pre-built role images | Create 6 Dockerfiles (one per role), new build-images.sh script |
| 6 | Update event logger | Remove tool-call event methods |
| 7 | Update tests | Delete 3 test files, rewrite 7, update 5 integration tests |
| 8 | Update docs and skills | Rewrite SKILL.md and role-specs, update context packets |

### Implementation Order

Phases 1-4 should be done together as a single pass (they're interdependent — you can't remove the dispatcher without updating the files that import it). Phase 5 (images) can be done independently. Phases 6-8 can be done in parallel after 1-4.

---

## Key Files to Read

### To understand the current system (READ FIRST):

| File | What it is |
|------|-----------|
| `~/.claude/mcp/ptc-server/server.py` | MCP entry point |
| `~/.claude/mcp/ptc-server/container_manager.py` | Container/REPL lifecycle — **most complex file, most changes** |
| `~/.claude/mcp/ptc-server/ipc_host.py` | Host-side socket server — gets heavily simplified |
| `~/.claude/mcp/ptc-server/ipc_protocol.py` | Message types — 3 get deleted |
| `~/.claude/mcp/ptc-server/sandbox_runtime/runtime.py` | Container entry point — gets simplified |
| `~/.claude/mcp/ptc-server/sandbox_runtime/executor.py` | Persistent namespace — tool stubs removed, eviction bug fixed |
| `~/.claude/mcp/ptc-server/config.json` | Configuration — heavily stripped |

### To understand what's being deleted (skim only):

| File | What it is |
|------|-----------|
| `~/.claude/mcp/ptc-server/tool_dispatcher.py` | 5-strategy routing — **entire file deleted** |
| `~/.claude/mcp/ptc-server/tool_registry.py` | Role→tool permissions — **entire file deleted** |
| `~/.claude/mcp/ptc-server/ipc_sandbox.py` | Container-side IPC client — **entire file deleted** |
| `~/.claude/mcp/ptc-server/ptc_tools/` | 7 modules, 15 tools — **entire directory deleted** |

### Background context:

| File | What it is |
|------|-----------|
| `~/personal/agentic_workflow/PTC-MCP-Hardening.md` | OpenSandbox comparison and 4 hardening upgrades |
| `~/personal/agentic_workflow/PTC-RESUME.md` | Original architecture orientation (will be outdated after changes) |
| `~/personal/agentic_workflow/ptc-ipc-consolidated.md` | Original design doc (~1930 lines) — archive after changes |
| `~/personal/ptc-benchmark/report.md` | Benchmark results: 88.5% token reduction, 19/27 task wins |

---

## Key Decisions Made (Do Not Revisit)

1. **Bridge networking enabled** — containers get full network access. Security hardening (gVisor, seccomp) deferred to later.
2. **IPC tool dispatch removed** — no internal tools, no pause-resume for tool calls. Container does everything directly via Python.
3. **Simplified IPC kept** — for code submission to persistent REPL. 5 message types (execute, complete, error, ping, pong).
4. **`/workspace` mounted read-write** — container code can write files directly.
5. **Pre-built role images** — no more runtime pip install. One Dockerfile per role.
6. **`run_tests`/`run_linter` via Bash** — agents use Claude Code's Bash tool directly, not PTC.
7. **nsjail evaluated but rejected** — requires per-execution sandboxing which breaks persistent namespace requirement. Docker REPL pool is the right approach for stateful multi-call agents.
8. **OpenSandbox integration rejected** — adds overhead (FastAPI server + execd + Jupyter per container) without solving problems PTC doesn't already handle. Cherry-picked ideas (pre-built images, TTL cleanup, readiness polling) instead.

---

## What NOT to Change

- **MCP server interface** — `ptc_execute(agent_id, role, code, timeout)` stays exactly the same
- **REPL pool architecture** — 6 REPLs per container, sub-agent routing via `_parent_map`
- **Namespace persistence** — variables survive across calls, memory eviction at 512MB
- **Event logger structure** — JSONL format, just removing tool-call events
- **Output capping** — 64KB max on print() output
- **Container lifecycle** — create, handoff, cleanup, stop_all

---

## Potential Gotchas

1. **`hooks/subagent_start.py`** (lines 395, 397) has hardcoded tool lists for role configs. This is functional code (not just docs) and will need updating.

2. **`ipc_host.py` `send_execute` signature change** — currently takes `role` parameter (for tool dispatch permission checking). After changes, `role` is unnecessary. Update the call in `container_manager.py:execute_code()` accordingly.

3. **`executor.py` `_execute_async()` method** — currently captures locals from the async wrapper and updates namespace. This is unrelated to tool dispatch and MUST be preserved. Don't accidentally delete it thinking it's IPC-related.

4. **`container_manager.py` `_start_repl_in_container()`** currently passes `--tools` CLI argument to runtime.py. After changes, this argument is gone. Make sure to also update `parse_args()` in runtime.py to not require it.

5. **Event logger `log_pip_install_start`/`log_pip_install_done`** — called from `_install_role_packages()` which is being deleted. Delete these logger methods too or they'll be dead code.

6. **The benchmark suite** (`~/personal/ptc-benchmark/`) has its own `ptc_wrapper.py` and `ptc_mcp_server.py` that reference old tools. These are in a separate repo and can be updated independently — don't let them block the main changes.
