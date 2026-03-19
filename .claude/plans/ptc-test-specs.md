# PTC Test Architecture Specification

**Feature:** PTC (Programmatic Tool Calling) MCP Server Rewrite
**Plan:** `.claude/plans/ptc-plan.json` (14 chunks)
**Design Doc:** `.claude/designs/ptc.md` (30 verification criteria)
**Date:** 2026-02-28

---

## Overview

This document defines the complete test architecture for the PTC feature. For each of the 14 chunks, it specifies:
- Exact test file and test function names
- Pass A (chunk-wise) test tiers
- Mock strategies and fixture requirements
- Chunk-coder handoff blocks for immediate TDD red-phase entry

Additionally, it covers:
- Shared fixtures for `tests/ptc/conftest.py`
- Pass B integration tests spanning multiple chunks
- Pass C system-level validation
- All 30 verification criteria mapped to specific test functions

**Test file layout:**

```
tests/ptc/
  conftest.py                  # Shared fixtures (created chunk-01, extended later)
  test_ipc_protocol.py         # chunk-01
  test_sandbox_runtime.py      # chunk-02 + chunk-03
  test_event_logger.py         # chunk-04
  test_ipc_host.py             # chunk-05 (+ chunk-13 additions)
  test_container_manager.py    # chunk-06 (+ chunk-13 additions)
  test_repl_pool.py            # chunk-07 (+ chunk-13 additions)
  test_ptc_tools.py            # chunk-08 + chunk-09
  test_tool_dispatcher.py      # chunk-10
  test_tool_registry.py        # chunk-11 (modify existing)
  test_ptc_execute.py          # chunk-12 (+ chunk-13 additions)
```

**Markers:**

| Mark | Purpose | Usage |
|------|---------|-------|
| `@pytest.mark.unit` | Isolated unit tests | All Pass A tests |
| `@pytest.mark.asyncio` | Async test functions | IPC, executor, container manager tests |
| `@pytest.mark.integration` | Cross-chunk tests | Pass B tests |
| `@pytest.mark.system` | End-to-end tests | Pass C tests |
| `@pytest.mark.golden` | Golden example tests | Exact input/output verification |
| `@pytest.mark.slow` | Tests >5s | Docker-involving tests in Pass C |

---

## Shared Fixtures (`tests/ptc/conftest.py`)

Created in chunk-01, extended as chunks are implemented. All fixtures are function-scoped unless noted.

### Message Factories

```
make_execute_msg(exec_id=None, code="print(1)")
  -> ExecuteMsg with auto-generated exec_id if None

make_tool_result_msg(call_id=None, content='{"ok":true}', error=None)
  -> ToolResultMsg

make_cancel_msg(exec_id=None, reason="timeout")
  -> CancelMsg

make_tool_call_msg(call_id=None, tool="read_file", input=None)
  -> ToolCallMsg with default input dict

make_complete_msg(exec_id=None, stdout="", stderr="", return_code=0, namespace_keys=None, namespace_size_kb=0)
  -> CompleteMsg

make_error_msg(exec_id=None, message="error", traceback="")
  -> ErrorMsg
```

### Mock IPC Client

```
mock_ipc_client
  -> AsyncMock with call_tool(tool_name, params) -> dict
  -> Configurable via mock_ipc_client.call_tool.return_value
```

### Mock Tool Dispatcher

```
mock_dispatcher
  -> AsyncMock with dispatch(role, tool_name, input, exec_id) -> str (JSON)
  -> Default return: json.dumps({"result": "ok"})
```

### Mock Event Logger

```
mock_event_logger
  -> MagicMock with all log_* methods as no-ops
  -> Tracks calls for assertion: mock_event_logger.log_exec_start.assert_called_once_with(...)
```

### Mock Docker Subprocess

```
mock_docker_exec(stdout="", stderr="", returncode=0)
  -> Patches asyncio.create_subprocess_exec
  -> Returns mock process with communicate() -> (stdout_bytes, stderr_bytes) and returncode

mock_docker_run(container_name="ptc-test-explorer-abc123")
  -> Patches docker run specifically, returns container name on stdout
```

### Mock Socket Streams (async)

```
mock_socket_pair()
  -> Returns (mock_reader, mock_writer) for asyncio stream simulation
  -> mock_reader.readline() returns pre-configured NDJSON lines
  -> mock_writer.write() captures written bytes for assertion
  -> mock_writer.drain() is async no-op
```

### Temp File Fixtures

```
tmp_project_dir(tmp_path)
  -> Creates a temp directory with minimal project structure:
     tmp_path/src/main.py (simple file)
     tmp_path/pyproject.toml (minimal)
  -> Returns tmp_path

tmp_log_path(tmp_path)
  -> Returns tmp_path / "ptc-events.jsonl"

tmp_socket_dir(tmp_path)
  -> Returns tmp_path / "sockets" (created)
```

### Config Fixture

```
ptc_config()
  -> Returns dict matching config.json schema with test-friendly values
  -> resource_limits.timeout_seconds = 5 (short for tests)
  -> resource_limits.max_containers = 2
  -> repl_pool.max_repls_per_container = 3
```

### Registry Fixture

```
mock_registry()
  -> MagicMock with is_allowed(role, tool) -> bool (default True)
  -> get_strategy(tool) -> str (returns from TOOL_STRATEGIES dict)
```

---

## Chunk-01: IPC Protocol Messages and Serialization

**Test file:** `tests/ptc/test_ipc_protocol.py`
**Mock strategy:** No mocks needed — pure dataclasses and functions
**Data strategy:** L1 golden (deterministic values)

### TestExecuteMsg (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_execute_msg_fields | exec_id="e-abc", code="x=1" | ExecuteMsg(exec_id, code) | .exec_id == "e-abc", .code == "x=1", .type == "execute" |
| test_execute_msg_round_trip | msg = ExecuteMsg("e-1", "print(1)") | decode(encode(msg)) | d["exec_id"] == "e-1", d["code"] == "print(1)", d["type"] == "execute" |
| test_execute_msg_default_type | No explicit type | ExecuteMsg("e-1", "x") | .type == "execute" |

### TestToolResultMsg (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_tool_result_msg_fields | call_id="c-abc", content='{"ok":true}' | ToolResultMsg(...) | .call_id, .content, .error is None, .type == "tool_result" |
| test_tool_result_msg_round_trip | msg with content and error=None | decode(encode(msg)) | round-trip preserves all fields |
| test_tool_result_msg_with_error | error="not found" | ToolResultMsg(..., error="not found") | d["error"] == "not found" |

### TestCancelMsg (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_cancel_msg_default_reason | exec_id="e-1" | CancelMsg("e-1") | .reason == "timeout" |
| test_cancel_msg_round_trip | msg with reason="user" | decode(encode(msg)) | d["reason"] == "user" |

### TestPingPong (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ping_msg_type | — | PingMsg() | .type == "ping" |
| test_pong_msg_type | — | PongMsg() | .type == "pong" |

### TestToolCallMsg (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_tool_call_msg_fields | call_id, tool="read_file", input={"path":"x"} | ToolCallMsg(...) | .tool, .input preserved |
| test_tool_call_msg_round_trip | full msg | decode(encode(msg)) | all fields preserved, input dict intact |

### TestCompleteMsg (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_complete_msg_defaults | exec_id only | CompleteMsg("e-1", stdout="hello") | .stderr == "", .return_code == 0, .namespace_keys == [], .namespace_size_kb == 0 |
| test_complete_msg_full | all fields set | CompleteMsg(...) | all values preserved |
| test_complete_msg_round_trip | full msg | decode(encode(msg)) | all fields round-trip |

### TestErrorMsg (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_error_msg_fields | exec_id, message, traceback | ErrorMsg(...) | .type == "error" |
| test_error_msg_round_trip | full msg | decode(encode(msg)) | fields preserved |

### TestEncodeDecode (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_encode_returns_bytes | ExecuteMsg | encode(msg) | isinstance(result, bytes), ends with b"\n" |
| test_encode_compact_json | ExecuteMsg | encode(msg) | no spaces in output (compact separators) |
| test_decode_strips_whitespace | encoded + extra whitespace | decode(line) | correctly parsed |
| test_encode_decode_unicode | code="x = 'hello world'" (unicode) | decode(encode(msg)) | unicode preserved |

### TestIdGeneration (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_new_exec_id_format | — | new_exec_id() | starts with "e-", len == 14, hex chars after prefix |
| test_new_call_id_format | — | new_call_id() | starts with "c-", len == 14 |
| test_id_uniqueness | — | set of 1000 new_exec_id() calls | len == 1000 (all unique) |

### TestConstants (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_max_message_size | — | import MAX_MESSAGE_SIZE | == 1_048_576 |

**Total chunk-01:** 25 tests

---

## Chunk-02: Code Executor with Persistent Namespace

**Test file:** `tests/ptc/test_sandbox_runtime.py`
**Mock strategy:** IPC client mocked (AsyncMock). No Docker, no sockets.
**Data strategy:** L1 golden

### TestExecutorInit (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_executor_init_creates_namespace | mock_ipc_client, tools=["read_file"] | Executor(ipc_client, tools) | ._namespace is dict, "read_file" in ._namespace |
| test_executor_init_no_tools | mock_ipc_client, tools=[] | Executor(ipc_client, []) | ._namespace is dict, no tool stubs |

### TestExecutorExecute (7 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_execute_simple_print | code="print(42)" | executor.execute(code) | stdout == "42\n", return_code == 0 |
| test_execute_captures_stderr | code with sys.stderr.write | executor.execute(code) | stderr contains expected text |
| test_execute_returns_namespace_keys | code="x = 1; y = 2" | executor.execute(code) | "x" in namespace_keys, "y" in namespace_keys |
| test_execute_returns_namespace_size_kb | code="data = 'a' * 10000" | executor.execute(code) | namespace_size_kb > 0 |
| test_execute_syntax_error | code="def(" | executor.execute(code) | return_code != 0, stderr contains "SyntaxError" |
| test_execute_runtime_error | code="1/0" | executor.execute(code) | return_code != 0, stderr contains "ZeroDivisionError" |
| test_execute_multiline | code with multiple statements and print | executor.execute(code) | stdout has all outputs |

### TestExecutorNamespacePersistence (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_namespace_persists_across_calls | exec "x=1" then exec "print(x)" | two execute() calls | second stdout == "1\n" |
| test_namespace_accumulates | exec "x=1", exec "y=2", exec "print(x+y)" | three calls | stdout == "3\n" |
| test_namespace_overwrite | exec "x=1", exec "x=2", exec "print(x)" | three calls | stdout == "2\n" |

### TestExecutorAwaitWrapping (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_await_detection | code="result = await read_file(path='x')" | executor.execute(code) | no error, mock ipc_client.call_tool called |
| test_no_await_runs_sync | code="x = 1 + 2" | executor.execute(code) | runs normally, no async wrapping |
| test_await_with_print | code="r = await read_file(path='x')\nprint(r)" | executor.execute(code) | stdout contains result, call_tool called |

### TestExecutorToolStubs (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_tool_stub_callable | tools=["read_file", "list_dir"] | Executor(ipc, tools) | both are callable in namespace |
| test_tool_stub_calls_ipc | tools=["read_file"], code with await | executor.execute(code) | ipc_client.call_tool.called_with("read_file", {...}) |
| test_tool_stub_returns_result | ipc returns {"content": "hello"} | executor.execute(await code) | result accessible in namespace |

### TestExecutorNamespaceSize (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_namespace_size_tracks_growth | exec large data assignment | executor.execute("big = 'x' * 100000") | namespace_size_kb > 0, increases after exec |
| test_namespace_keys_listed | exec "a=1; b=2; c=3" | executor.execute(code) | set(namespace_keys) >= {"a", "b", "c"} |

**Total chunk-02:** 20 tests

---

## Chunk-03: Container-side IPC Client + Runtime

**Test file:** `tests/ptc/test_sandbox_runtime.py` (extend, separate test classes)
**Mock strategy:** asyncio streams mocked (mock_socket_pair). No real sockets, no Docker.
**Data strategy:** L1 golden

### TestIpcClientCallTool (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_call_tool_sends_tool_call_msg | mock socket, configure response | await client.call_tool("read_file", {"path": "x"}) | writer received ToolCallMsg bytes, result is dict |
| test_call_tool_routes_by_call_id | mock socket, 2 responses (out of order) | two concurrent call_tool calls | each gets correct result matched by call_id |
| test_call_tool_raises_tool_error | response has error="denied" | await client.call_tool(...) | raises ToolError with message |
| test_call_tool_timeout | no response within timeout | await client.call_tool(...) with timeout | raises asyncio.TimeoutError or equivalent |

### TestIpcClientResponseHandler (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_response_handler_routes_multiple | 3 ToolResultMsg on mock reader | start handler, await 3 futures | all 3 futures resolved with correct data |
| test_response_handler_ignores_unknown_ids | ToolResultMsg with unknown call_id | handler processes | no crash, unknown msg logged or ignored |

### TestRuntimeMainLoop (5 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_runtime_processes_execute_msg | mock socket sends ExecuteMsg(code="print(1)") | run main loop (single iteration) | CompleteMsg sent back with stdout="1\n" |
| test_runtime_handles_cancel_msg | mock socket sends CancelMsg | process cancel | no crash, execution stops |
| test_runtime_handles_ping | mock socket sends PingMsg | main loop processes | PongMsg sent back |
| test_runtime_error_returns_error_msg | ExecuteMsg with code="1/0" | main loop | ErrorMsg sent back with traceback |
| test_runtime_socket_arg_parsing | --socket repl-2.sock | parse args | socket path set to /ptc_ipc/repl-2.sock |

**Total chunk-03:** 11 tests

---

## Chunk-04: Event Logger (JSONL Observability)

**Test file:** `tests/ptc/test_event_logger.py`
**Mock strategy:** No mocks — uses tmp_path for log file. Pure file I/O.
**Data strategy:** L1 golden

### TestEventLoggerInit (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_init_creates_parent_dirs | log_path with nested dirs | PtcEventLogger(log_path) | parent dirs exist |
| test_init_stores_path | log_path | PtcEventLogger(log_path) | .log_path == log_path |

### TestEmitEnvelope (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_emit_writes_jsonl_line | logger | _emit("test_event", "agent-1", "e-1", "ctr-1", {"key": "val"}) | file has 1 line, json.loads succeeds |
| test_emit_envelope_has_required_fields | logger | _emit(...) | parsed line has "ts", "event", "agent_id", "data" |
| test_emit_omits_empty_strings | logger | _emit("ev", "", "", "", {"k": "v"}) | parsed line does NOT have "agent_id" key (or has it removed) |
| test_emit_append_only | logger, emit twice | two _emit calls | file has 2 lines |

### TestContainerLifecycleMethods (6 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_log_container_create | logger | log_container_create("a1", "ctr-1", "explorer", "ptc-sandbox:latest", 1024) | event == "container_create", data has role, image, memory_mb |
| test_log_container_ready | logger | log_container_ready("a1", "ctr-1", 500, 3000, 16) | event == "container_ready", data has startup_ms, pip_install_ms |
| test_log_container_stop | logger | log_container_stop(...) | event == "container_stop" |
| test_log_container_crash | logger | log_container_crash(...) | event == "container_crash", data has reason, memory_at_crash_mb |
| test_log_container_health | logger | log_container_health(...) | event == "container_health" |
| test_log_container_handoff | logger | log_container_handoff(...) | event == "container_handoff", data has old/new agent_id |

### TestReplPoolMethods (6 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_log_repl_start | logger | log_repl_start("a1", "ctr-1", 0, None, False) | event == "repl_start", data has repl_index, is_sub_agent |
| test_log_repl_stop | logger | log_repl_stop(...) | event == "repl_stop" |
| test_log_repl_crash | logger | log_repl_crash(...) | event == "repl_crash" |
| test_log_pip_install_start | logger | log_pip_install_start(...) | event == "pip_install_start", data has packages |
| test_log_pip_install_done | logger | log_pip_install_done(...) | event == "pip_install_done", data has duration_ms |
| test_log_pip_install_error | logger | log_pip_install_error(...) | event == "pip_install_error", data has package, error |

### TestExecutionMethods (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_log_exec_start | logger | log_exec_start("a1", "e-1", 100, True) | event == "exec_start", data has code_length, has_await |
| test_log_exec_complete | logger | log_exec_complete("a1", "e-1", 250, 64, 2, ["x","y"], 10) | event == "exec_complete", data has duration_ms, tool_calls |
| test_log_exec_error | logger | log_exec_error(...) | event == "exec_error" |
| test_log_exec_timeout | logger | log_exec_timeout(...) | event == "exec_timeout" |

### TestToolDispatchMethods (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_log_tool_call_start | logger | log_tool_call_start("e-1", "read_file", {"path": "x"}) | event == "tool_call_start" |
| test_log_tool_call_complete | logger | log_tool_call_complete("e-1", "read_file", 50, 1024, "long result...") | event == "tool_call_complete" |
| test_log_tool_call_complete_truncates_preview | logger | call with 300-char result_preview | data["result_preview"] length <= 200 |
| test_log_tool_call_denied | logger | log_tool_call_denied("e-1", "run_tests", "explorer", "not allowed") | event == "tool_call_denied", data has role, reason |

### TestIpcMethods (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_log_ipc_connect | logger | log_ipc_connect("a1", "/path/sock") | event == "ipc_connect" |
| test_log_ipc_disconnect | logger | log_ipc_disconnect("a1", "eof", 2) | event == "ipc_disconnect", data has pending_calls |
| test_log_ipc_ping | logger | log_ipc_ping("a1", 15) | event == "ipc_ping", data has latency_ms |

### TestJqQueryability (1 golden test)

| Test | Input | Expected |
|------|-------|----------|
| test_events_queryable_with_jq | emit 5 events, 3 with exec_id="e-target" | subprocess `jq 'select(.exec_id=="e-target")'` returns exactly 3 lines |

**Total chunk-04:** 30 tests

---

## Chunk-05: IPC Host (Host-side Socket Server)

**Test file:** `tests/ptc/test_ipc_host.py`
**Mock strategy:** asyncio streams (mock_socket_pair), tool dispatcher (mock_dispatcher), event logger (mock_event_logger). No real sockets.
**Data strategy:** L1 golden for protocol tests, L2 config-adaptive for timeouts

### TestIpcHostInit (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ipc_host_init | socket_path, mock_dispatcher, mock_logger | IpcHost(...) | .socket_path set, .tool_dispatcher set |
| test_ipc_host_start_creates_socket | tmp_socket_dir | await host.start() | socket file exists (or server started) |

### TestIpcHostSendExecute (5 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_send_execute_simple_complete | mock reader returns CompleteMsg | await host.send_execute("e-1", "x=1", "explorer", 60) | returns CompleteMsg with stdout |
| test_send_execute_with_tool_call | reader returns ToolCallMsg then CompleteMsg | await host.send_execute(...) | dispatcher.dispatch called, ToolResultMsg sent, CompleteMsg returned |
| test_send_execute_with_error_msg | reader returns ErrorMsg | await host.send_execute(...) | returns ErrorMsg |
| test_send_execute_timeout_sends_cancel | reader never sends complete (hangs) | await host.send_execute(..., timeout=0.1) | CancelMsg written to socket, timeout error returned |
| test_send_execute_logs_events | mock_logger, normal flow | await host.send_execute(...) | logger.log_exec_start called, logger.log_exec_complete called |

### TestIpcHostParallelToolCalls (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_parallel_tool_calls_dispatched_concurrently | reader sends 3 ToolCallMsg before any result | host processes | dispatcher.dispatch called 3 times, all 3 ToolResultMsg sent back |
| test_parallel_tool_calls_independent_results | 2 ToolCallMsg, dispatcher returns different results | host processes | each ToolResultMsg has correct call_id and result |
| test_parallel_tool_calls_one_fails | 2 ToolCallMsg, one dispatch raises | host processes | failed call gets error ToolResultMsg, other succeeds |

### TestIpcHostHealthCheck (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_health_check_success | reader returns PongMsg | await host.health_check() | returns True |
| test_health_check_timeout | reader never responds | await host.health_check() | returns False |

### TestIpcHostStop (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_stop_closes_connection | started host | await host.stop() | writer.close called |
| test_stop_removes_socket_file | started host with socket file | await host.stop() | socket file removed |

**Total chunk-05:** 14 tests

---

## Chunk-06: Container Manager Core

**Test file:** `tests/ptc/test_container_manager.py`
**Mock strategy:** Docker CLI (mock_docker_exec/mock_docker_run), IpcHost (mocked), event logger (mock). No real Docker.
**Data strategy:** L1 golden for commands, L2 config-adaptive for resource limits

### TestDataclasses (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_repl_handle_fields | all args | ReplHandle("a1", 0, mock_ipc, datetime.now()) | .agent_id, .repl_index, .exec_count == 0 |
| test_container_info_defaults | required args only | ContainerInfo("ctr-1", "explorer", datetime.now(), datetime.now(), {}) | .max_repls == 6, .packages_installed == False |
| test_container_info_with_repls | repls dict with 2 entries | ContainerInfo(..., repls=repls) | len(.repls) == 2 |

### TestContainerCreation (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_create_container_docker_command | mock_docker_run | await mgr._create_container("explorer") | subprocess called with: docker run -d --name, --memory 1024m, --network none, --tmpfs, -v mounts |
| test_create_container_mounts_project_ro | mock_docker_run | await mgr._create_container("coder") | -v {project}:/workspace:ro in command |
| test_create_container_logs_event | mock_docker_run, mock_logger | await mgr._create_container("explorer") | logger.log_container_create called |
| test_create_container_returns_container_info | mock_docker_run | result = await mgr._create_container("explorer") | isinstance(result, ContainerInfo), result.role == "explorer" |

### TestPipInstall (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_pip_install_runs_docker_exec | mock_docker_exec | await mgr._install_role_packages("ctr-1", "explorer") | docker exec command contains "pip install" and explorer packages |
| test_pip_install_logs_events | mock_docker_exec, mock_logger | await mgr._install_role_packages(...) | log_pip_install_start and log_pip_install_done called |
| test_pip_install_sets_flag | mock_docker_exec | await mgr._install_role_packages("ctr-1", "explorer") | container.packages_installed == True |

### TestReplStart (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_start_repl_docker_exec_command | mock_docker_exec, mock IpcHost | await mgr._start_repl_in_container(container, "a1") | docker exec -d contains "runtime.py --socket repl-0.sock" |
| test_start_repl_creates_ipc_host | mock_docker_exec | await mgr._start_repl_in_container(...) | ReplHandle.ipc is IpcHost instance |
| test_start_repl_waits_for_health_check | mock_docker_exec, mock IpcHost.health_check returns True | start REPL | health_check called before returning |

### TestGetOrCreateRepl (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_get_existing_repl | agent already has REPL | await mgr.get_or_create_repl("a1", "explorer") | returns existing ReplHandle, no docker run |
| test_create_new_container_and_repl | no existing container | await mgr.get_or_create_repl("a1", "explorer") | docker run called, pip install called, REPL started |
| test_get_or_create_registers_agent | new agent | await mgr.get_or_create_repl("a1", "explorer") | _agent_to_container["a1"] set |

### TestExecuteCode (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_execute_code_delegates_to_ipc | agent has REPL with mock ipc | await mgr.execute_code("a1", "print(1)", 30) | repl.ipc.send_execute called with code |
| test_execute_code_acquires_lock | agent has REPL | two concurrent execute_code calls | serialized (lock acquired) |

### TestStopAndCleanup (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_stop_container | container exists | await mgr.stop_container("ctr-1") | docker rm -f called, container removed from tracking |
| test_stop_all | 2 containers | await mgr.stop_all() | both stopped |
| test_health_check_delegates | agent has REPL | await mgr.health_check("a1") | repl.ipc.health_check called |

### TestRolePackages (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_role_packages_has_all_roles | — | import ROLE_PACKAGES | set(keys) == {"explorer","coder","auditor","tester","researcher","strategist"} |
| test_role_packages_explorer_count | — | ROLE_PACKAGES["explorer"] | len == 16 |

**Total chunk-06:** 23 tests

---

## Chunk-07: REPL Pool + Handoff

**Test file:** `tests/ptc/test_repl_pool.py`
**Mock strategy:** Docker CLI mocked, IpcHost mocked. Tests focus on routing logic, not Docker I/O.
**Data strategy:** L1 golden

### TestSubAgentRouting (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_sub_agent_routes_to_parent_container | parent "a1" has container, register_parent("sub-1", "a1") | await mgr.get_or_create_repl("sub-1", "explorer") | REPL started in parent's container, NO new docker run |
| test_sub_agent_gets_new_repl_index | parent has repl-0 | get_or_create_repl("sub-1", ...) | sub-agent gets repl-1, parent still has repl-0 |
| test_sub_agent_unregistered_creates_new | no parent registered | get_or_create_repl("sub-1", "explorer") | creates new container (falls through to normal path) |

### TestReplIsolation (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_separate_ipc_hosts | parent + sub-agent in same container | check repl handles | parent.ipc != sub_agent.ipc (different IpcHost instances) |
| test_separate_socket_paths | parent + sub-agent | check socket paths | parent uses repl-0.sock, sub uses repl-1.sock |

### TestHandoff (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_handoff_preserves_container | old agent "a1" has container | await mgr.handle_handoff("a1", "a2") | same container_name, container not recreated |
| test_handoff_kills_old_repl | old agent has repl-0 | await mgr.handle_handoff("a1", "a2") | docker exec kill for old REPL process, old REPL removed |
| test_handoff_starts_fresh_repl | old → new | await mgr.handle_handoff("a1", "a2") | new REPL started for "a2", new IpcHost created |
| test_handoff_logs_event | mock_logger | await mgr.handle_handoff("a1", "a2") | logger.log_container_handoff called with old/new agent_id |

### TestIdleReuse (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_idle_container_reused_same_role | idle explorer container (no active REPLs) | get_or_create_repl("a2", "explorer") | reuses existing container, no docker run |
| test_idle_container_not_reused_different_role | idle explorer container | get_or_create_repl("a2", "coder") | creates NEW container (role mismatch) |
| test_idle_reuse_starts_new_repl | idle container | reuse | new REPL started, agent registered |

### TestMaxReplEnforcement (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_max_repls_rejected | container at max_repls_per_container (e.g., 3) | start another REPL | raises error or returns error dict |
| test_below_max_repls_allowed | container with 2 of 3 max | start another REPL | succeeds |

### TestStopRepl (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_stop_repl_kills_only_that_repl | container with 2 REPLs | await mgr.stop_repl("sub-1") | sub-1 REPL removed, parent REPL still active |
| test_stop_repl_container_survives | container with 2 REPLs, stop one | check | container still in _containers dict |

### TestConcurrentExecution (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_concurrent_repls_execute_independently | 2 REPLs in one container | await asyncio.gather(exec_code("a1",...), exec_code("sub-1",...)) | both return results, no interference |

**Total chunk-07:** 17 tests

---

## Chunk-08: ptc_tools Library — Direct-Call Functions

**Test file:** `tests/ptc/test_ptc_tools.py`
**Mock strategy:** Uses tmp_project_dir for file operations. No mocks for pure-function tools.
**Data strategy:** L1 golden

### TestProjectRoot (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_get_project_root_host | running on host | get_project_root() | returns valid path, not /workspace |
| test_max_file_size_constant | — | import MAX_FILE_SIZE | is int, > 0 |
| test_default_encoding | — | import DEFAULT_ENCODING | == "utf-8" |

### TestReadFile (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_read_file_returns_dict | tmp file with "hello\nworld" | read_file(path, project_root) | result has keys: path, lines, size_bytes, content, truncated |
| test_read_file_content | tmp file with known content | read_file(...) | result["content"] == known content, result["lines"] == 2 |
| test_read_file_not_found | non-existent path | read_file("no_such.py", root) | result has error key or raises |
| test_read_file_truncated | file > MAX_FILE_SIZE | read_file(large_file, root) | result["truncated"] == True |

### TestWriteFile (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_write_file_creates | new path, content="hello" | write_file(path, content, root) | file exists, result has path, size_bytes, written=True |
| test_write_file_overwrites | existing file | write_file(path, "new", root) | content replaced |
| test_write_file_returns_dict | — | write_file(...) | result keys: path, size_bytes, written |

### TestListDir (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_list_dir_returns_entries | tmp_project_dir with files | list_dir(".", root) | result is dict with entries |
| test_list_dir_max_depth | nested dirs 4 levels deep | list_dir(".", root, max_depth=2) | only 2 levels shown |
| test_list_dir_hidden_excluded | dir with .hidden file | list_dir(".", root, include_hidden=False) | .hidden not in result |

### TestSearchCode (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_search_code_finds_matches | file containing "def foo" | search_code("def foo", root) | result has matches, total_matches >= 1 |
| test_search_code_no_matches | search for "nonexistent_xyz" | search_code(..., root) | total_matches == 0 |
| test_search_code_max_results | 100 matches, max_results=5 | search_code(..., root, max_results=5) | len(matches) <= 5, truncated == True |

### TestAnalyzeImports (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_analyze_imports_returns_dict | tmp project with imports | analyze_imports("**/*.py", root) | result has files_analyzed, internal_deps, external_deps |
| test_analyze_imports_detects_stdlib | file with "import os" | analyze_imports(...) | external_deps includes "os" or detected |

### TestAnalyzeStructure (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_analyze_structure_finds_classes | file with class Foo | analyze_structure("**/*.py", root) | "Foo" in classes |
| test_analyze_structure_finds_functions | file with def bar | analyze_structure(...) | "bar" in functions |

### TestCountTokens (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_count_tokens_returns_dict | tmp file | count_tokens(["file.py"], root) | result is dict with token count > 0 |
| test_count_tokens_multiple_files | 2 tmp files | count_tokens([f1, f2], root) | aggregated count |

**Total chunk-08:** 22 tests

---

## Chunk-09: ptc_tools — Subprocess + Host Functions

**Test file:** `tests/ptc/test_ptc_tools.py` (extend with new test classes)
**Mock strategy:** subprocess.run / asyncio subprocess mocked. Git tests use tmp git repo.
**Data strategy:** L1 golden

### TestRunTests (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_run_tests_returns_dict | mock subprocess returns pytest output | run_tests("tests/", root) | result has: passed, failed, errors, failures, duration_seconds |
| test_run_tests_with_markers | markers="unit" | run_tests("tests/", root, markers="unit") | "-m unit" in subprocess command |
| test_run_tests_failure | subprocess returns exit code 1 | run_tests(...) | result["failed"] > 0 |

### TestRunLinter (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_run_linter_returns_dict | mock subprocess success | run_linter(["src/"], root) | result is dict |
| test_run_linter_with_fix | fix=True | run_linter(["src/"], root, fix=True) | "--fix" in command |

### TestCoverageSummary (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_coverage_summary_returns_dict | mock subprocess | coverage_summary("tests/", root) | result is dict |
| test_coverage_summary_with_source | source_dirs="src" | coverage_summary(..., source_dirs="src") | source dir in command |

### TestGitDiffSummary (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_git_diff_summary_returns_dict | tmp git repo with changes | git_diff_summary("main", root) | result has: base_branch, files_changed, insertions, deletions |
| test_git_diff_summary_default_branch | — | git_diff_summary(project_root=root) | base_branch == "main" |
| test_git_diff_summary_no_changes | clean repo | git_diff_summary(...) | files_changed == 0 |

### TestDiffSummary (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_diff_summary_returns_dict | tmp git repo | diff_summary("HEAD~1", root) | result is dict |
| test_diff_summary_custom_head | head_ref="HEAD~2" | diff_summary("main", root, head_ref="HEAD~2") | uses custom head |

### TestListChanges (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_list_changes_returns_dict | tmp git repo with commit | list_changes("HEAD~1", root) | result is dict with changes |
| test_list_changes_default_ref | — | list_changes(project_root=root) | since_ref == "HEAD~1" |

### TestWebFetchSummary (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_web_fetch_summary_returns_dict | mock subprocess | web_fetch_summary("https://example.com", root) | result is dict |
| test_web_fetch_summary_with_prompt | prompt="summarize" | web_fetch_summary(url, root, prompt="summarize") | prompt passed |

### TestMcpCallSummary (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_mcp_call_summary_stub | — | mcp_call_summary("server", "tool", root) | returns dict (stub behavior) |

### TestInitReexports (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_all_functions_importable_from_init | — | from ptc_tools import read_file, write_file, ..., mcp_call_summary | all 15 importable |

**Total chunk-09:** 18 tests

---

## Chunk-10: Tool Dispatcher

**Test file:** `tests/ptc/test_tool_dispatcher.py`
**Mock strategy:** Registry (mock_registry), ptc_tools functions (patch), event logger (mock). No real dispatch.
**Data strategy:** L1 golden for routing, L2 config-adaptive for concurrency

### TestDispatcherInit (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_dispatcher_init | registry, root, logger, max_concurrent=4 | ToolDispatcher(...) | ._semaphore._value == 4 |

### TestStrategyRouting (6 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_dispatch_ptc_tools_strategy | registry says strategy="ptc_tools", tool="read_file" | await dispatch("explorer", "read_file", {...}, "e-1") | ptc_tools_dispatch called with read_file |
| test_dispatch_subprocess_strategy | strategy="subprocess", tool="run_tests" | await dispatch("coder", "run_tests", {...}, "e-1") | subprocess_dispatch called |
| test_dispatch_host_fs_strategy | strategy="host_fs", tool="write_file" | await dispatch("coder", "write_file", {...}, "e-1") | host_fs_dispatch called |
| test_dispatch_host_network_strategy | strategy="host_network", tool="web_fetch_summary" | await dispatch("researcher", "web_fetch_summary", {...}, "e-1") | host_network_dispatch called |
| test_dispatch_mcp_proxy_strategy | strategy="mcp_proxy", tool="mcp_call_summary" | await dispatch(...) | returns stub result |
| test_dispatch_returns_json_string | any valid dispatch | result = await dispatch(...) | json.loads(result) succeeds |

### TestPermissionDenial (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_permission_denied_returns_error | registry.is_allowed returns False | await dispatch("explorer", "run_tests", {}, "e-1") | result is JSON with "error" key, contains "denied" |
| test_permission_denied_logs_event | mock_logger | denied dispatch | logger.log_tool_call_denied called with role and tool |
| test_permission_denied_does_not_dispatch | registry denies | dispatch | underlying dispatch function NOT called |

### TestSemaphoreBackpressure (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_semaphore_limits_concurrency | max_concurrent=2, 4 concurrent dispatches | await asyncio.gather(4 dispatches) | at most 2 run concurrently (check via timing or mock) |
| test_semaphore_releases_after_dispatch | dispatch completes | check semaphore | value returns to max_concurrent after all complete |

### TestEventLogging (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_dispatch_logs_start_and_complete | mock_logger | successful dispatch | log_tool_call_start and log_tool_call_complete called |
| test_dispatch_logs_error_on_failure | dispatch raises | handle error | log_tool_call_error called |

**Total chunk-10:** 14 tests

---

## Chunk-11: Docker Image + Config + Registry Updates

**Test file:** `tests/ptc/test_tool_registry.py` (modify existing)
**Mock strategy:** Config loaded from fixture. Dockerfile checked via file existence. No Docker build in tests.
**Data strategy:** L1 golden

### TestDockerfile (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_dockerfile_exists | — | check file | ~/.claude/mcp/ptc-server/Dockerfile exists |
| test_dockerfile_base_image | — | read Dockerfile | contains "FROM python:3.11-slim" |

### TestBuildImageScript (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_build_script_exists | — | check file | build-image.sh exists and is executable |

### TestConfigSchema (5 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_config_has_all_sections | load config.json | check keys | role_tools, tool_strategies, resource_limits, repl_pool, docker, ipc, observability all present |
| test_config_role_tools_has_execute | config.role_tools | each role | "execute" in role's tools list |
| test_config_tool_strategies_complete | config.tool_strategies | check | all 15 tools mapped to strategies |
| test_config_resource_limits | config.resource_limits | check | memory_mb, timeout_seconds, max_containers all present and > 0 |
| test_config_repl_pool | config.repl_pool | check | max_repls_per_container > 0 |

### TestRegistryExecutePermission (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_registry_allows_execute_all_roles | registry with new config | is_allowed(role, "execute") for each role | all True |
| test_registry_denies_unlisted_tool | registry | is_allowed("explorer", "run_tests") | False (explorer doesn't have run_tests) |

### TestRegistryGetStrategy (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_get_strategy_read_file | registry | get_strategy("read_file") | == "ptc_tools" |
| test_get_strategy_run_tests | registry | get_strategy("run_tests") | == "subprocess" |
| test_get_strategy_write_file | registry | get_strategy("write_file") | == "host_fs" |

**Total chunk-11:** 13 tests

---

## Chunk-12: MCP Server Rewrite

**Test file:** `tests/ptc/test_ptc_execute.py`
**Mock strategy:** ContainerManager (mock), ToolDispatcher (mock), EventLogger (mock), Registry (mock). No real Docker, no real MCP server.
**Data strategy:** L1 golden

### TestPtcExecute (6 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ptc_execute_returns_stdout | mock container_manager.execute_code returns CompleteMsg with stdout="42" | await ptc_execute("a1", "explorer", "print(42)") | result contains "42" |
| test_ptc_execute_invalid_role | — | await ptc_execute("a1", "invalid_role", "x") | result contains error about invalid role |
| test_ptc_execute_docker_unavailable | docker_available flag = False | await ptc_execute(...) | result contains "Docker" error |
| test_ptc_execute_respects_timeout | timeout=10 | await ptc_execute("a1", "explorer", "x", timeout=10) | execute_code called with timeout=10 |
| test_ptc_execute_default_timeout_from_config | timeout=0 (default) | await ptc_execute("a1", "explorer", "x") | uses config timeout |
| test_eager_startup_creates_container_before_execute | mock container_manager, track call order | server startup sequence | get_or_create_repl called during init/startup, NOT lazily on first ptc_execute. Verifies eager container+pip+REPL readiness per design doc. |

### TestOutputCap (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_output_cap_under_limit | stdout = "x" * 1000 | _cap_output(stdout, 65536) | unchanged |
| test_output_cap_at_limit | stdout = "x" * 65536 | _cap_output(stdout, 65536) | truncated, contains truncation notice |
| test_output_cap_over_limit | stdout = "x" * 100000 | _cap_output(stdout, 65536) | len(result) <= 65536 + truncation notice |

### TestPtcStatus (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ptc_status_specific_agent | agent_id="a1" | await ptc_status("a1") | result contains agent health info |
| test_ptc_status_all | agent_id="" | await ptc_status() | result contains all container status |

### TestPtcResetNamespace (1 test)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ptc_reset_namespace | agent has container | await ptc_reset_namespace("a1") | container_manager.reset_namespace called |

### TestPtcShutdown (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ptc_shutdown | agent has container | await ptc_shutdown("a1") | container_manager resolved agent_id to container, stop called |
| test_ptc_shutdown_all | — | await ptc_shutdown_all() | container_manager.stop_all called |

### TestMcpToolRegistration (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_ptc_execute_registered | import server | check mcp tools | "ptc_execute" in registered tools |
| test_old_tools_removed | import server | check mcp tools | none of 15 old tool names registered |

**Total chunk-12:** 16 tests

---

## Chunk-13: Error Handling + Hardening + Cleanup

**Tests added across multiple existing test files.**
**Mock strategy:** Same as parent chunks, with specific failure injection.
**Data strategy:** L1 golden for error scenarios

### Additions to `test_ipc_host.py` (5 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_crash_during_execution_returns_error | reader raises ConnectionResetError (EOF) | send_execute | returns error dict with "crashed", NOT re-executed |
| test_crash_rejects_all_pending_futures | 2 pending tool calls, then EOF | socket dies | both futures rejected with ContainerCrashError |
| test_per_tool_timeout_sends_error | dispatcher takes >timeout | dispatch one tool | ToolResultMsg with error sent back, code gets ToolError |
| test_overall_timeout_sends_cancel | execution takes >deadline | send_execute with short timeout | CancelMsg sent to container |
| test_max_tool_calls_exceeded | counter > config max | 20 tool calls in one execution | cancel sent after limit, partial result returned |

### Additions to `test_container_manager.py` (4 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_crash_during_idle_restarts | health_check returns False (stale) | next execute_code call | container restarted, warning in result |
| test_pip_install_timeout | pip takes too long | _install_role_packages with short timeout | logged as error, container usable for stdlib |
| test_pip_install_failure | pip returns exit code 1 | _install_role_packages | logged with package name, packages_installed remains False |
| test_idle_timeout_destroys_container | container idle > timeout_seconds, no active REPLs | background cleanup task | container destroyed, logged |

### Additions to `test_sandbox_runtime.py` (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_namespace_eviction_at_512mb | namespace with large objects > 512MB | executor.execute(...) | oldest non-callable objects evicted, size reduced |
| test_namespace_eviction_preserves_callables | namespace has functions + large data | eviction triggers | functions preserved, data evicted |

### Additions to `test_repl_pool.py` (2 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_repl_crash_only_affects_that_repl | container with 2 REPLs, REPL-1 socket EOF | process crash | REPL-1 agent gets error, REPL-0 unaffected, container alive |
| test_repl_crash_logged | REPL crash | process | logger.log_repl_crash called |

### Additions to `test_ptc_execute.py` (3 tests)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_docker_unavailable_check | mock docker info fails | ptc_execute(...) | returns clear error mentioning Docker |
| test_all_error_results_are_json | various error scenarios | ptc_execute | all return valid JSON with "error" key |
| test_execution_error_has_traceback | code raises exception | ptc_execute | result includes traceback preview |

### Cleanup Verification (2 tests in `test_ptc_execute.py`)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_no_make_kernel_code_in_tool_implementations | — | grep -r "make_kernel_code" in tool_implementations/ | 0 matches |
| test_old_test_files_deleted | — | check paths | test_kernel_manager.py, test_server.py (old), test_tools.py (old) do not exist |

**Total chunk-13:** 18 tests

---

## Chunk-14: PTC Skills Documentation

**No test file — documentation only chunk.**
**Verification via existence invariants (shell commands).**

### Existence Checks (3 invariants)

| Check | Command | Assert |
|-------|---------|--------|
| SKILL.md exists with frontmatter | grep 'name: ptc-sandbox' skills/ptc-sandbox/SKILL.md | exit 0 |
| All 6 role-spec files | ls skills/ptc-sandbox/role-specs/*.md \| wc -l | == 6 |
| Reference docs exist | ls skills/ptc-sandbox/references/*.md \| wc -l | == 2 |

**Total chunk-14:** 0 tests (invariants only)

---

## Pass B: Holistic Integration Tests

These tests span multiple chunks and verify cross-component behavior. Implemented during chunk-13 (hardening chunk) or as a dedicated integration test pass.

**Test file:** `tests/ptc/test_integration.py` (created during chunk-13)

### B.1 IPC Round-Trip Integration (covers chunks 01+02+03+05)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_full_ipc_round_trip | IpcHost + IpcClient connected via real Unix socket pair (no Docker) | send ExecuteMsg → code calls tool → ToolCallMsg → dispatch → ToolResultMsg → CompleteMsg | CompleteMsg received with correct stdout |
| test_ipc_parallel_tool_dispatch | code does asyncio.gather(tool1(), tool2()) | execute via IPC | both tools dispatched, both results returned, CompleteMsg has correct output |
| test_ipc_round_trip_with_error | code raises exception | execute via IPC | ErrorMsg returned with traceback |

### B.2 Container Lifecycle Integration (covers chunks 06+07+05+04)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_container_create_pip_repl_execute | mock Docker, full lifecycle | create → pip install → start REPL → execute code | code result returned, all events logged |
| test_handoff_full_lifecycle | mock Docker | create → execute → handoff → execute again | second agent gets fresh namespace, same container |
| test_sub_agent_shares_container | mock Docker, register parent | parent creates container → sub-agent gets REPL → both execute | same container_name, different repl indices |

### B.3 Tool Dispatch End-to-End (covers chunks 08+09+10)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_dispatch_all_15_tools | ToolDispatcher with real registry, mock ptc_tools | dispatch each of 15 tools | correct strategy used, result returned for each |
| test_dispatch_with_permission_check | registry from config | dispatch allowed tool + denied tool | allowed succeeds, denied returns error JSON |

### B.4 Config Sensitivity (covers chunk-11 + chunk-10)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_timeout_config_affects_execution | config.timeout=1 vs config.timeout=60 | execute with delay | short timeout triggers cancel, long timeout completes |
| test_max_containers_config_enforced | config.max_containers=1 | create 2 containers | second creation rejected or queued |
| test_semaphore_config_respected | config max_concurrent=2 | 4 dispatches | only 2 concurrent |

**Total Pass B:** 11 tests

---

## Pass C: System-Level Validation

**Activate:** Yes — this feature is a multi-component IPC system with Docker containers, socket communication, and concurrent execution. Pass C is essential.

**Test file:** `tests/ptc/test_system.py`
**Marks:** `@pytest.mark.system`, `@pytest.mark.slow`

### C.1 End-to-End ptc_execute Flow (with mock Docker)

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_e2e_ptc_execute_simple | full server stack (mocked Docker) | ptc_execute("a1", "explorer", "print(1+1)") | returns "2\n" |
| test_e2e_ptc_execute_with_tool_call | full stack, dispatcher returns real result | ptc_execute with "result = await read_file(path='test.py')\nprint(result)" | stdout contains file content |
| test_e2e_ptc_execute_persistent_state | full stack | call 1: "x = 42", call 2: "print(x)" | call 2 stdout == "42\n" |
| test_e2e_ptc_execute_role_denied | full stack | ptc_execute("a1", "explorer", "await run_tests(...)") | error about permission denied |

### C.2 Observability Verification

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_e2e_events_logged_for_full_flow | full stack, tmp log file | ptc_execute with tool call | log file has container_create, repl_start, exec_start, tool_call_start, tool_call_complete, exec_complete events in order |
| test_e2e_events_have_correlation_ids | full stack | ptc_execute | all events for same execution share exec_id |

### C.3 Error Recovery Scenarios

| Test | Arrange | Act | Assert |
|------|---------|-----|--------|
| test_e2e_timeout_recovery | full stack, code sleeps | ptc_execute with timeout=1 | timeout error returned, container still usable for next call |
| test_e2e_crash_recovery_idle | full stack, container "dies" between calls | ptc_execute → kill container → ptc_execute again | second call gets warning about restart, still works |

**Total Pass C:** 8 tests

---

## Verification Criteria Mapping

All 30 verification criteria from the design doc mapped to specific test functions.

| # | Criterion | Test Function(s) | Test File |
|---|-----------|------------------|-----------|
| 1 | IPC round-trip | test_full_ipc_round_trip, test_send_execute_simple_complete | test_integration.py, test_ipc_host.py |
| 2 | Parallel tools | test_ipc_parallel_tool_dispatch, test_parallel_tool_calls_dispatched_concurrently | test_integration.py, test_ipc_host.py |
| 3 | Backpressure (semaphore 4) | test_semaphore_limits_concurrency, test_semaphore_config_respected | test_tool_dispatcher.py, test_integration.py |
| 4 | Persistent state | test_namespace_persists_across_calls, test_e2e_ptc_execute_persistent_state | test_sandbox_runtime.py, test_system.py |
| 5 | Namespace eviction 512MB | test_namespace_eviction_at_512mb, test_namespace_eviction_preserves_callables | test_sandbox_runtime.py |
| 6 | Role enforcement | test_permission_denied_returns_error, test_e2e_ptc_execute_role_denied | test_tool_dispatcher.py, test_system.py |
| 7 | Timeout cancellation | test_send_execute_timeout_sends_cancel, test_overall_timeout_sends_cancel | test_ipc_host.py |
| 8 | Output cap 64KB | test_output_cap_at_limit, test_output_cap_over_limit | test_ptc_execute.py |
| 9 | Base image build | test_dockerfile_exists, test_dockerfile_base_image | test_tool_registry.py |
| 10 | Runtime pip install | test_pip_install_runs_docker_exec, test_pip_install_logs_events | test_container_manager.py |
| 11 | Eager startup | test_eager_startup_creates_container_before_execute | test_ptc_execute.py |
| 12 | Package availability | test_build_script_exists (verified by build-image.sh) | test_tool_registry.py |
| 13 | ptc_tools importable | test_all_functions_importable_from_init | test_ptc_tools.py |
| 14 | Docker unavailable | test_docker_unavailable_check, test_ptc_execute_docker_unavailable | test_ptc_execute.py |
| 15 | REPL start <500ms | test_start_repl_waits_for_health_check (timing assertion) | test_container_manager.py |
| 16 | Multiple REPLs independent | test_concurrent_repls_execute_independently | test_repl_pool.py |
| 17 | REPL isolation | test_separate_ipc_hosts, test_separate_socket_paths | test_repl_pool.py |
| 18 | Sub-agent routing | test_sub_agent_routes_to_parent_container | test_repl_pool.py |
| 19 | REPL cleanup | test_stop_repl_kills_only_that_repl, test_stop_repl_container_survives | test_repl_pool.py |
| 20 | Max REPLs enforcement | test_max_repls_rejected, test_below_max_repls_allowed | test_repl_pool.py |
| 21 | Handoff preserves container | test_handoff_preserves_container | test_repl_pool.py |
| 22 | Handoff resets namespace | test_handoff_starts_fresh_repl | test_repl_pool.py |
| 23 | Container reuse | test_idle_container_reused_same_role, test_idle_container_not_reused_different_role | test_repl_pool.py |
| 24 | Idle timeout | test_idle_timeout_destroys_container | test_container_manager.py |
| 25 | Crash during execution | test_crash_during_execution_returns_error, test_crash_rejects_all_pending_futures | test_ipc_host.py |
| 26 | Crash during idle | test_crash_during_idle_restarts | test_container_manager.py |
| 27 | REPL crash isolation | test_repl_crash_only_affects_that_repl | test_repl_pool.py |
| 28 | All events in JSONL | test_e2e_events_logged_for_full_flow, all test_log_* tests | test_event_logger.py, test_system.py |
| 29 | REPL events logged | test_log_repl_start, test_log_repl_stop, test_log_repl_crash | test_event_logger.py |
| 30 | Queryable with jq | test_events_queryable_with_jq | test_event_logger.py |

**Coverage:** All 30 criteria have at least one unit test AND most have an integration/system test as backup.

---

## Chunk-Coder Handoff Summaries

Each block below gives a chunk-coder everything needed to start the TDD red phase immediately.

---

### Handoff: chunk-01 — IPC Protocol

**Test file:** `tests/ptc/test_ipc_protocol.py`
**Also create:** `tests/ptc/conftest.py` (initial version with message factories)

**Tests to write first (25 total):**
- `TestExecuteMsg`: test_execute_msg_fields, test_execute_msg_round_trip, test_execute_msg_default_type
- `TestToolResultMsg`: test_tool_result_msg_fields, test_tool_result_msg_round_trip, test_tool_result_msg_with_error
- `TestCancelMsg`: test_cancel_msg_default_reason, test_cancel_msg_round_trip
- `TestPingPong`: test_ping_msg_type, test_pong_msg_type
- `TestToolCallMsg`: test_tool_call_msg_fields, test_tool_call_msg_round_trip
- `TestCompleteMsg`: test_complete_msg_defaults, test_complete_msg_full, test_complete_msg_round_trip
- `TestErrorMsg`: test_error_msg_fields, test_error_msg_round_trip
- `TestEncodeDecode`: test_encode_returns_bytes, test_encode_compact_json, test_decode_strips_whitespace, test_encode_decode_unicode
- `TestIdGeneration`: test_new_exec_id_format, test_new_call_id_format, test_id_uniqueness
- `TestConstants`: test_max_message_size

**Fixtures to use:** make_execute_msg, make_tool_call_msg, etc. (create in conftest.py)
**What to mock:** Nothing — pure dataclass tests
**Expected failures before implementation:** All 25 tests fail with ImportError (ipc_protocol module doesn't exist)

---

### Handoff: chunk-02 — Code Executor

**Test file:** `tests/ptc/test_sandbox_runtime.py`

**Tests to write first (20 total):**
- `TestExecutorInit`: test_executor_init_creates_namespace, test_executor_init_no_tools
- `TestExecutorExecute`: test_execute_simple_print, test_execute_captures_stderr, test_execute_returns_namespace_keys, test_execute_returns_namespace_size_kb, test_execute_syntax_error, test_execute_runtime_error, test_execute_multiline
- `TestExecutorNamespacePersistence`: test_namespace_persists_across_calls, test_namespace_accumulates, test_namespace_overwrite
- `TestExecutorAwaitWrapping`: test_await_detection, test_no_await_runs_sync, test_await_with_print
- `TestExecutorToolStubs`: test_tool_stub_callable, test_tool_stub_calls_ipc, test_tool_stub_returns_result
- `TestExecutorNamespaceSize`: test_namespace_size_tracks_growth, test_namespace_keys_listed

**Fixtures to use:** mock_ipc_client (AsyncMock with call_tool)
**What to mock:** IPC client only — `AsyncMock()` with `call_tool.return_value = {"content": "test"}`
**Expected failures:** ImportError for sandbox_runtime.executor

---

### Handoff: chunk-03 — IPC Client + Runtime

**Test file:** `tests/ptc/test_sandbox_runtime.py` (add new classes, don't modify chunk-02 tests)

**Tests to write first (11 total):**
- `TestIpcClientCallTool`: test_call_tool_sends_tool_call_msg, test_call_tool_routes_by_call_id, test_call_tool_raises_tool_error, test_call_tool_timeout
- `TestIpcClientResponseHandler`: test_response_handler_routes_multiple, test_response_handler_ignores_unknown_ids
- `TestRuntimeMainLoop`: test_runtime_processes_execute_msg, test_runtime_handles_cancel_msg, test_runtime_handles_ping, test_runtime_error_returns_error_msg, test_runtime_socket_arg_parsing

**Fixtures to use:** mock_socket_pair() — mock asyncio.StreamReader/StreamWriter
**What to mock:** asyncio streams (configure mock_reader.readline to return encoded NDJSON messages)
**Expected failures:** ImportError for ipc_sandbox.IpcClient, sandbox_runtime.runtime

---

### Handoff: chunk-04 — Event Logger

**Test file:** `tests/ptc/test_event_logger.py`

**Tests to write first (30 total):**
- `TestEventLoggerInit`: 2 tests
- `TestEmitEnvelope`: 4 tests
- `TestContainerLifecycleMethods`: 6 tests
- `TestReplPoolMethods`: 6 tests
- `TestExecutionMethods`: 4 tests
- `TestToolDispatchMethods`: 4 tests (including truncation test)
- `TestIpcMethods`: 3 tests
- `TestJqQueryability`: 1 golden test

**Fixtures to use:** tmp_log_path (from conftest)
**What to mock:** Nothing — uses real file I/O on tmp_path
**Expected failures:** ImportError for event_logger.PtcEventLogger

---

### Handoff: chunk-05 — IPC Host

**Test file:** `tests/ptc/test_ipc_host.py`

**Tests to write first (14 total):**
- `TestIpcHostInit`: 2 tests
- `TestIpcHostSendExecute`: 5 tests (including timeout)
- `TestIpcHostParallelToolCalls`: 3 tests
- `TestIpcHostHealthCheck`: 2 tests
- `TestIpcHostStop`: 2 tests

**Fixtures to use:** mock_socket_pair, mock_dispatcher, mock_event_logger, tmp_socket_dir
**What to mock:** asyncio streams (reader/writer), tool dispatcher (AsyncMock), event logger
**Expected failures:** ImportError for ipc_host.IpcHost
**Note:** All tests are `@pytest.mark.asyncio`. Use `asyncio.open_unix_connection` mocking pattern.

---

### Handoff: chunk-06 — Container Manager

**Test file:** `tests/ptc/test_container_manager.py`

**Tests to write first (23 total):**
- `TestDataclasses`: 3 tests
- `TestContainerCreation`: 4 tests
- `TestPipInstall`: 3 tests
- `TestReplStart`: 3 tests
- `TestGetOrCreateRepl`: 3 tests
- `TestExecuteCode`: 2 tests
- `TestStopAndCleanup`: 3 tests
- `TestRolePackages`: 2 tests

**Fixtures to use:** mock_docker_exec, mock_docker_run, mock_event_logger, mock_registry, ptc_config, tmp_socket_dir
**What to mock:** `asyncio.create_subprocess_exec` (for docker commands), IpcHost (mock the class), event logger
**Expected failures:** ImportError for container_manager

---

### Handoff: chunk-07 — REPL Pool + Handoff

**Test file:** `tests/ptc/test_repl_pool.py`

**Tests to write first (17 total):**
- `TestSubAgentRouting`: 3 tests
- `TestReplIsolation`: 2 tests
- `TestHandoff`: 4 tests
- `TestIdleReuse`: 3 tests
- `TestMaxReplEnforcement`: 2 tests
- `TestStopRepl`: 2 tests
- `TestConcurrentExecution`: 1 test

**Fixtures to use:** mock_docker_exec, mock_event_logger, pre-configured ContainerManager with mock Docker
**What to mock:** Docker CLI subprocess, IpcHost instances
**Expected failures:** Routing logic not yet implemented in container_manager.py
**Precondition:** chunk-06 complete (ContainerManager exists)

---

### Handoff: chunk-08 — ptc_tools Direct-Call

**Test file:** `tests/ptc/test_ptc_tools.py`

**Tests to write first (22 total):**
- `TestProjectRoot`: 3 tests
- `TestReadFile`: 4 tests
- `TestWriteFile`: 3 tests
- `TestListDir`: 3 tests
- `TestSearchCode`: 3 tests
- `TestAnalyzeImports`: 2 tests
- `TestAnalyzeStructure`: 2 tests
- `TestCountTokens`: 2 tests

**Fixtures to use:** tmp_project_dir (creates temp directory with test files)
**What to mock:** Nothing for file-based tools (use tmp_path). No subprocess mocking needed.
**Expected failures:** ImportError for ptc_tools.files, ptc_tools.search, ptc_tools.tokens

---

### Handoff: chunk-09 — ptc_tools Subprocess + Host

**Test file:** `tests/ptc/test_ptc_tools.py` (extend with new test classes)

**Tests to write first (18 total):**
- `TestRunTests`: 3 tests
- `TestRunLinter`: 2 tests
- `TestCoverageSummary`: 2 tests
- `TestGitDiffSummary`: 3 tests
- `TestDiffSummary`: 2 tests
- `TestListChanges`: 2 tests
- `TestWebFetchSummary`: 2 tests
- `TestMcpCallSummary`: 1 test
- `TestInitReexports`: 1 test

**Fixtures to use:** tmp_project_dir, mock subprocess (via unittest.mock.patch)
**What to mock:** `subprocess.run` or `asyncio.create_subprocess_exec` for testing/git/network tools. Git tests can use real tmp git repo (git init + commit).
**Expected failures:** ImportError for ptc_tools.testing, ptc_tools.git, ptc_tools.network

---

### Handoff: chunk-10 — Tool Dispatcher

**Test file:** `tests/ptc/test_tool_dispatcher.py`

**Tests to write first (14 total):**
- `TestDispatcherInit`: 1 test
- `TestStrategyRouting`: 6 tests
- `TestPermissionDenial`: 3 tests
- `TestSemaphoreBackpressure`: 2 tests
- `TestEventLogging`: 2 tests

**Fixtures to use:** mock_registry, mock_event_logger, ptc_config
**What to mock:** Registry (is_allowed, get_strategy), ptc_tools functions (patch at module level), event logger
**Expected failures:** ImportError for tool_dispatcher.ToolDispatcher

---

### Handoff: chunk-11 — Docker Image + Config + Registry

**Test file:** `tests/ptc/test_tool_registry.py` (modify existing)

**Tests to write first (13 total):**
- `TestDockerfile`: 2 tests
- `TestBuildImageScript`: 1 test
- `TestConfigSchema`: 5 tests
- `TestRegistryExecutePermission`: 2 tests
- `TestRegistryGetStrategy`: 3 tests

**Fixtures to use:** ptc_config fixture, path to ptc-server dir
**What to mock:** Nothing for file checks. Registry tests use real config loaded from fixture.
**Expected failures:** Dockerfile doesn't exist, config.json missing new sections, get_strategy not implemented
**Precondition:** Existing test_tool_registry.py may have tests — extend, don't break existing.

---

### Handoff: chunk-12 — MCP Server Rewrite

**Test file:** `tests/ptc/test_ptc_execute.py`

**Tests to write first (16 total):**
- `TestPtcExecute`: 6 tests (including test_eager_startup_creates_container_before_execute)
- `TestOutputCap`: 3 tests
- `TestPtcStatus`: 2 tests
- `TestPtcResetNamespace`: 1 test
- `TestPtcShutdown`: 2 tests
- `TestMcpToolRegistration`: 2 tests

**Fixtures to use:** mock ContainerManager, mock ToolDispatcher, mock EventLogger, mock Registry
**What to mock:** All major components (ContainerManager.execute_code, get_or_create_repl, etc.)
**Expected failures:** Old server.py has 15 tools; new server.py doesn't exist yet
**Precondition:** chunks 06, 07, 10, 11 complete
**API note:** `ptc_shutdown(agent_id)` in server.py must resolve agent_id to container_name internally before calling ContainerManager. Whether this is a `stop(agent_id)` convenience method or inline resolution is an implementation choice — the test asserts the agent's container is stopped.

---

### Handoff: chunk-13 — Error Handling + Hardening

**Tests added to:** test_ipc_host.py (5), test_container_manager.py (4), test_sandbox_runtime.py (2), test_repl_pool.py (2), test_ptc_execute.py (5)
**Also create:** `tests/ptc/test_integration.py` (11 Pass B tests), `tests/ptc/test_system.py` (8 Pass C tests)

**Tests to write first (18 chunk-specific + 11 Pass B + 8 Pass C = 37 total):**

Chunk-13 specific (18):
- In test_ipc_host.py: crash tests, timeout tests, max tool calls
- In test_container_manager.py: idle crash restart, pip install failures, idle timeout
- In test_sandbox_runtime.py: namespace eviction
- In test_repl_pool.py: REPL crash isolation
- In test_ptc_execute.py: Docker unavailable, JSON errors, cleanup verification

Pass B integration (11): See Pass B section above
Pass C system (8): See Pass C section above

**Fixtures to use:** All existing fixtures + failure injection helpers (configure mocks to raise, timeout, EOF)
**What to mock:** Same as parent chunks, with specific failure scenarios injected
**Expected failures:** Error handling code not yet implemented in production modules
**Precondition:** chunk-12 complete (full system assembled)

---

### Handoff: chunk-14 — PTC Skills Documentation

**No tests to write.** This chunk produces markdown documentation only.
**Verification:** Shell commands checking file existence (see invariants in plan).

---

## Test Architecture Summary

### Pass A: Chunk-wise Tests

| Chunk | Core Logic | Golden | Edge Case | Negative | Log-Assert | Total |
|-------|-----------|--------|-----------|----------|------------|-------|
| chunk-01 | 16 | 7 | 2 | 0 | 0 | 25 |
| chunk-02 | 14 | 2 | 2 | 2 | 0 | 20 |
| chunk-03 | 6 | 1 | 2 | 2 | 0 | 11 |
| chunk-04 | 28 | 1 | 1 | 0 | 0 | 30 |
| chunk-05 | 10 | 0 | 2 | 1 | 1 | 14 |
| chunk-06 | 20 | 0 | 1 | 0 | 2 | 23 |
| chunk-07 | 12 | 0 | 2 | 2 | 1 | 17 |
| chunk-08 | 14 | 2 | 5 | 1 | 0 | 22 |
| chunk-09 | 15 | 0 | 3 | 0 | 0 | 18 |
| chunk-10 | 9 | 0 | 0 | 3 | 2 | 14 |
| chunk-11 | 12 | 0 | 0 | 1 | 0 | 13 |
| chunk-12 | 12 | 0 | 2 | 2 | 0 | 16 |
| chunk-13 | 8 | 0 | 0 | 9 | 1 | 18 |
| chunk-14 | 0 | 0 | 0 | 0 | 0 | 0 |
| **Total** | **176** | **13** | **22** | **23** | **7** | **241** |

### Pass B: Holistic Tests

| Scope | Integration | Config-Sensitivity | Total |
|-------|-------------|-------------------|-------|
| IPC round-trip | 3 | 0 | 3 |
| Container lifecycle | 3 | 0 | 3 |
| Tool dispatch | 2 | 0 | 2 |
| Config sensitivity | 0 | 3 | 3 |
| **Total** | **8** | **3** | **11** |

### Pass C: System-Level Validation

| Category | Tests | Marks |
|----------|-------|-------|
| E2E ptc_execute | 4 | @system |
| Observability | 2 | @system |
| Error recovery | 2 | @system, @slow |
| **Total** | **8** | |

### Test Infrastructure

**Fixtures (conftest.py):**
- make_*_msg: 6 message factory fixtures
- mock_ipc_client: AsyncMock IPC client
- mock_dispatcher: AsyncMock tool dispatcher
- mock_event_logger: MagicMock logger with all log_* methods
- mock_docker_exec/run: Patched subprocess for Docker
- mock_socket_pair: Mock asyncio StreamReader/Writer
- tmp_project_dir: Temp project with files
- tmp_log_path: Temp JSONL log path
- tmp_socket_dir: Temp socket directory
- ptc_config: Test-friendly config dict
- mock_registry: Mock tool registry

**Marks:**
- @pytest.mark.unit: Isolated unit tests
- @pytest.mark.asyncio: Async test functions
- @pytest.mark.integration: Cross-chunk tests
- @pytest.mark.system: System-level tests
- @pytest.mark.golden: Golden example tests
- @pytest.mark.slow: Long-running tests

### Quality Metrics

**Assertion strength:** 176 core logic, 13 golden, 7 log-based, 3 config-sensitivity
**Data strategy:** 200+ L1 golden, 10 L2 config-adaptive, 0 L3 property-based
**Defensive coverage:** 23 negative paths tested, 22 edge cases
**Verification criteria:** 30/30 mapped to specific test functions

### Grand Total

| Category | Count |
|----------|-------|
| Pass A (chunk-wise) | 241 |
| Pass B (integration) | 11 |
| Pass C (system) | 8 |
| **Total test functions** | **260** |
