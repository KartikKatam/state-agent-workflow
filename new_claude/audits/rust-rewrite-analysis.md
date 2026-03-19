# Rust Rewrite Analysis

**Date:** 2026-02-26
**Auditor:** System Audit Agent (Opus 4.6)

---

## Executive Summary

**Verdict: Yes, rewrite hot-path components.** The evidence overwhelmingly favors rewriting hooks and the daemon in Rust. The 50x startup improvement on hooks applies to every tool call. The 8-10x memory reduction on the daemon matters for long sessions. The system's architecture (subprocess hooks + socket daemon) makes this a clean replacement -- no FFI needed.

---

## 1. Performance Case

### Startup Time (Most Critical Metric)

Hooks fire on **every tool call**. Python startup overhead dominates.

| Platform | Rust Binary | Python 3 Interpreter | Ratio |
|----------|------------|---------------------|-------|
| Intel Core i5-2400S | **0.51 ms** | 25.84 ms | **~50x** |
| Raspberry Pi 3 | **4.42 ms** | 197.79 ms | **~45x** |

Additional Python overhead:
- `import pydantic` alone: ~200ms
- `pre_tool_use.py` already avoids Pydantic (noted in its docstring) -- stdlib-only gives ~30ms startup
- `post_tool_use.py` imports 4 internal modules + conditional Pydantic: ~60-100ms startup

**Per-session impact:** ~200 tool calls * ~50ms saved = **~10 seconds** saved per session. For long sessions with 1000+ tool calls: **~50+ seconds**.

**Source:** [startup-time benchmarks](https://github.com/bdrung/startup-time) | [Python Startup Time (Victor's Notes)](https://pythondev.readthedocs.io/startup_time.html)

### Memory (Daemon)

The daemon runs for the entire session (up to 3600s).

| Scenario | Python | Rust | Ratio |
|----------|--------|------|-------|
| Idle daemon (Kafka consumer comparison) | 89-100 MB | 10-10.8 MB | **8-10x** |
| Per-connection overhead | OS thread (~8MB stack) | Tokio task (~KB) | **~1000x** |
| GIL contention | Yes (ThreadingMixIn) | No | N/A |

**Source:** [Python vs Rust Memory (Kafka)](https://medium.com/@nunocarvalhodossantos/rust-vs-python-ram-and-cpu-application-comparison-in-kafka-consumers-8869195a1087) | [Async Memory Benchmarks](https://pkolaczk.github.io/memory-consumption-of-async/)

### JSON Parsing

| Validator | Speed vs Python | Notes |
|-----------|----------------|-------|
| Rust `jsonschema` crate | **45-240x faster** | Complex schemas |
| serde_json (typed) | **6-15x faster** than Pydantic v2 | Standard deserialization |
| Rust serde (fast valid) | **1.41x** | Simple case |
| Rust serde (fast invalid) | **15.23x** | Error path |

**Source:** [jsonschema-rs benchmarks](https://github.com/Stranger6667/jsonschema) | [serde-rs/json-benchmark](https://github.com/serde-rs/json-benchmark)

---

## 2. Real-World Python-to-Rust Rewrites

### ruff (Python linter)

| Metric | Python (flake8) | Rust (ruff) | Improvement |
|--------|----------------|-------------|-------------|
| CPython codebase lint | ~20s | **~0.2s** | **100x** |
| Single file re-check (cached) | several seconds | **60ms** | Orders of magnitude |
| Pre-commit hook time | 18s (multiple tools) | **0.3s** (ruff) | **60x** |

### uv (Package manager)

| Operation | pip | uv | Improvement |
|-----------|-----|-----|-------------|
| Install JupyterLab (cold) | 21.4s | **2.6s** | **8x** |
| Create virtualenv (with seed) | 141.4ms | **4.1ms** | **34x** |
| Warm cache operations | baseline | — | **80-115x** |

### Key Lesson

> "Profile first -- focus on the most performance-critical 20% of code that delivers 80% of gains." All successful rewrites (ruff, uv, ripgrep) targeted the hot path, not the entire codebase.

**Sources:** [Astral/ruff](https://astral.sh/ruff) | [uv benchmarks](https://github.com/astral-sh/uv/blob/main/BENCHMARKS.md) | [ripgrep benchmarks](https://burntsushi.net/ripgrep/)

---

## 3. Component-by-Component Assessment

### Priority 1: pre_tool_use (HIGHEST IMPACT)

| Factor | Assessment |
|--------|-----------|
| Current startup | ~30ms (stdlib-only, already optimized) |
| Rust startup | **<1ms** |
| Migration complexity | **LOW** -- self-contained, stdlib-only, 259 lines |
| Dependencies | `json`, `os`, `re`, `socket`, `sys` -- all trivial in Rust |
| Socket client | `std::os::unix::net::UnixStream` -- 1:1 mapping |
| Regex | `regex` crate -- drop-in replacement |
| JSON | `serde_json` -- faster than Python `json` |
| Risk | **Very low** -- permissive fallback already handles failures |
| Effort | **1 week** |

**Why first:** Fires on EVERY tool call. Self-contained (no internal imports). Clear JSON stdin/stdout contract. Already avoids Pydantic for performance -- Rust eliminates the remaining Python overhead.

### Priority 2: workflow_state Daemon (HIGH IMPACT)

| Factor | Assessment |
|--------|-----------|
| Current memory | ~89-100 MB idle |
| Rust memory | **~10 MB idle** |
| Current architecture | `ThreadingMixIn + UnixStreamServer` |
| Rust architecture | `tokio::net::UnixListener` + async tasks |
| Migration complexity | **MEDIUM** -- 807 lines, clean dispatch table |
| Socket protocol | Newline-delimited JSON -- serde handles this |
| State machine engine | HashMap-based transition lookup |
| Risk | **Low** -- socket protocol is simple, easy to test |
| Effort | **2-3 weeks** |

**Mapping:**

| Python | Rust |
|--------|------|
| `socketserver.UnixStreamServer` | `tokio::net::UnixListener` |
| `socketserver.ThreadingMixIn` | `tokio::spawn` (async tasks) |
| `RequestHandler.handle()` | `async fn handle_connection(stream)` |
| `json.loads` / `json.dumps` | `serde_json::from_str` / `serde_json::to_string` |
| `threading.Event` (shutdown) | `tokio::sync::watch` channel |
| `signal.signal(SIGTERM, ...)` | `tokio::signal::unix::signal(SignalKind::terminate())` |
| `Pydantic.model_validate_json` | `serde_json::from_str::<T>` with derive macros |

**Why second:** Long-running process benefits from memory reduction. Eliminates GIL contention. The `process_request` dispatch table maps to Rust `match` -- zero overhead. Guards can be implemented in Rust from the start.

### Priority 3: post_tool_use (MEDIUM IMPACT)

| Factor | Assessment |
|--------|-----------|
| Current startup | ~60-100ms (imports 4 internal modules + conditional Pydantic) |
| Rust startup | **<1ms** |
| Migration complexity | **MEDIUM-HIGH** -- 701 lines, 4 utility imports, dispatch table |
| Dependencies | context_monitor, event_logger, schema_validator, state_helpers |
| Risk | **Low** -- handlers are independent; can port incrementally |
| Effort | **2-3 weeks** |

**Why third:** More complex than pre_tool_use (imports 4 utility modules). But fires on every tool call, so startup improvement compounds. Schema validation becomes `serde_json::from_str` -- no separate validator needed.

### Priority 4: Other Hooks (LOW STANDALONE IMPACT)

| Hook | Frequency | Rewrite Benefit | Effort |
|------|-----------|-----------------|--------|
| `session_start.py` | Once per session | Low (startup time irrelevant) | 2 weeks |
| `subagent_start.py` | Per sub-agent | Low-Medium (5-20 per session) | 2 weeks |
| `subagent_stop.py` | Per sub-agent | Low-Medium | 1 week |
| `stop.py` | Once per session | Negligible | 1 week |
| `pre_compact.py` | 1-3 per session | Negligible | 1 week |

**Recommendation:** Don't rewrite these unless doing a full system Rust migration. The per-session hooks fire too infrequently for startup time to matter.

### DO NOT REWRITE

| Component | Reason |
|-----------|--------|
| Agent definitions (`.claude/agents/*.md`) | Markdown, no execution |
| Skills (`.claude/skills/`) | Prompt engineering, no execution |
| `scripts/convert_design.py` | Runs once per workflow |
| `scripts/generate_phase_report.py` | Runs once per phase |
| `scripts/gate.sh` | Shell, invokes other tools |
| State machine JSON files | Data, not code |
| Schemas (Pydantic) | Serve as source-of-truth specification. Keep Python models; generate Rust structs from them. |

---

## 4. Rust State Machine Libraries

| Library | Approach | Fit for Our System |
|---------|----------|--------------------|
| **sm** | Compile-time, type-encoded | **No** -- our machines are JSON-defined, runtime-loaded |
| **smlang** | Proc macro DSL, async support | **Partial** -- only if hardcoding machines |
| **rust_fsm** | Trait-based | **No** -- not flexible enough for JSON definitions |

**Recommendation:** Custom runtime engine. Our state machines are data-driven. The right Rust approach is:
1. serde deserialization of existing JSON definitions
2. HashMap-based transition lookup engine
3. Guard evaluation as a trait with registered implementations

This preserves JSON configurability while gaining Rust's speed.

**Sources:** [smlang-rs](https://github.com/korken89/smlang-rs) | [sm](https://github.com/rustic-games/sm) | [rust-fsm](https://docs.rs/rust-fsm/)

---

## 5. Migration Strategy

### Phase 1: pre_tool_use Binary (Week 1-2)

```bash
cargo init agentic-hooks
cd agentic-hooks
```

Core crates:
- `serde`, `serde_json` -- JSON handling
- `regex` -- pattern matching
- `tokio` (features: `["net"]`) -- Unix socket client (or `std::os::unix::net` for sync)

Steps:
1. Port the 3-tier waterfall check to Rust
2. Port regex patterns for hard blocks and ask patterns
3. Port socket client for daemon query
4. Compile to `target/release/pre_tool_use`
5. Update `.claude/settings.json` to point to Rust binary
6. Benchmark: measure hook invocation time before/after

### Phase 2: Daemon Binary (Week 3-5)

Core crates:
- `tokio` (features: `["full"]`) -- async runtime, Unix socket server
- `serde`, `serde_json` -- JSON handling
- `signal-hook` -- graceful shutdown
- `tracing` -- structured logging (replaces our event_logger)

Steps:
1. Define Rust structs matching our Pydantic models (AgentState, StateMachineDefinition, WorkflowState)
2. Port `process_request` dispatch to Rust `match`
3. Port `do_transition` with guard evaluation (implement guards from the start!)
4. Port `check_tool_allowed` with write glob matching
5. Use `tokio::net::UnixListener` instead of `socketserver`
6. Atomic file writes via `std::fs::rename` (fix the race condition in the port)
7. Benchmark: memory usage, request latency

### Phase 3: post_tool_use Binary (Week 5-7)

Steps:
1. Port `HookContext` to Rust struct
2. Port dispatch table handlers
3. Bundle schema validation (serde structs, no separate validator)
4. Bundle event logging (tracing crate)
5. Bundle context monitoring
6. Benchmark: end-to-end hook latency

### Phase 4: Integration Testing (Week 7-8)

1. Run Python and Rust hooks in parallel, compare JSON output
2. Verify exact behavioral equivalence
3. Switch production to Rust binaries
4. Keep Python as fallback for 2 weeks

---

## 6. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Rust learning curve | Medium | Codebase is well-structured; mostly JSON handling + socket I/O |
| Build complexity | Low | Single `cargo build --release` produces static binaries |
| Schema drift (Pydantic vs serde) | Medium | Generate Rust structs from Pydantic models, or maintain both with CI checks |
| Debug difficulty | Low | Rust error messages are excellent; serde errors are descriptive |
| Rollback path | Low | Permissive fallback design means both can coexist during migration |
| Platform portability | Medium | Cross-compile for Linux/macOS; Windows likely not needed |

---

## 7. Benchmark Summary

| Metric | Python | Rust | Improvement | Source |
|--------|--------|------|-------------|--------|
| Binary startup | 26-198ms | 0.5-4.4ms | **50x** | [startup-time](https://github.com/bdrung/startup-time) |
| Linting (CPython) | 20s | 0.2s | **100x** | [ruff](https://astral.sh/ruff) |
| Package install (cold) | 21.4s | 2.6s | **8x** | [uv benchmarks](https://github.com/astral-sh/uv/blob/main/BENCHMARKS.md) |
| Virtualenv creation | 141ms | 4.1ms | **34x** | [uv benchmarks](https://github.com/astral-sh/uv/blob/main/BENCHMARKS.md) |
| Idle daemon memory | 89-100MB | 10-10.8MB | **8-10x** | [Kafka benchmark](https://medium.com/@nunocarvalhodossantos/rust-vs-python-ram-and-cpu-application-comparison-in-kafka-consumers-8869195a1087) |
| JSON schema validation | baseline | 45-240x faster | **45-240x** | [jsonschema-rs](https://github.com/Stranger6667/jsonschema) |
| Grep (line numbers) | 9.48s | 1.66s | **5.7x** | [ripgrep](https://burntsushi.net/ripgrep/) |
| Pre-commit hooks | 18s | 0.3s | **60x** | [ruff migration reports](https://pythonspeed.com/articles/pylint-flake8-ruff/) |

---

## 8. Skip PyO3 Unless Necessary

The system's architecture (subprocess hooks + socket daemon) means Rust binaries **replace** Python scripts cleanly. No FFI bridge needed.

PyO3 would only be needed if:
- Python code needs to call Rust functions at the library level
- `validate_merge_readiness.py` or `generate_phase_report.py` want to use Rust validation

Currently neither scenario applies. The hot-path components are standalone binaries with JSON contracts.

**Sources:** [PyO3 User Guide](https://pyo3.rs/) | [Incrementally Porting Python to Rust](https://blog.waleedkhan.name/port-python-to-rust/) | [Making Python 100x Faster with Rust](https://ohadravid.github.io/posts/2023-03-rusty-python/)
