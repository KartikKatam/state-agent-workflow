# Best Practices Comparison Report

**Date:** 2026-02-26
**Auditor:** System Audit Agent (Opus 4.6)

---

## Executive Summary

This report compares the agentic workflow system's implementation against industry best practices discovered through comprehensive research on Claude Code hooks, state machine enforcement, schema design, and inter-agent communication patterns. The system is architecturally ahead of most open-source agentic frameworks but has significant gaps in guard enforcement, error visibility, and schema validation depth.

---

## 1. Hook Architecture

### Our Approach

- 7 hooks covering all major lifecycle events (SessionStart, PreToolUse, PostToolUse, SubagentStart, SubagentStop, Stop, PreCompact)
- 3-tier enforcement model in PreToolUse (hard block / ask user / state machine gating)
- Standalone Python scripts invoked as subprocesses
- Permissive fallback on all failures
- Universal `except Exception: pass` error handling

### Industry Best Practices

**Handler Type Diversity** (Source: [Claude Code Hooks Guide](https://code.claude.com/docs/en/hooks-guide))
- Three handler types available: `command`, `prompt`, `agent`
- `prompt` hooks: single-turn LLM evaluation for semantic decisions
- `agent` hooks: multi-turn sub-agents with tool access for deep verification
- **Gap:** Our system uses only `command` type hooks. No `prompt` or `agent` hooks for semantic validation (e.g., "does this code match the plan?").

**Async Hooks** (Source: [JP Caparas - Dev Genius](https://blog.devgenius.io/claude-code-async-hooks-what-they-are-and-when-to-use-them-61b21cd71aad))
- `async: true` runs hooks without blocking the agent
- Reported 3x faster workflows for non-blocking side effects
- Only `command` type supports async
- **Gap:** Our PostToolUse does logging, event emission, and ruff linting synchronously on every tool call. Event logging and non-critical tracking should be async.

**Error Handling** (Source: [Blake Crosley - 95 Hooks](https://blakecrosley.com/en/blog/claude-code-hooks))
- Best hooks come from incidents, not planning
- Hooks provide deterministic guarantees on probabilistic systems
- Categories: safety, formatting, context, lifecycle
- **Gap:** Our universal `except Exception: pass` provides deterministic non-failure but zero diagnostic visibility. Blake Crosley's 95-hook system implies error logging for hook failures.

**Permission Decision Priority** (Source: [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks))
- Deny > Ask > Allow > Default Ask
- Any deny overrides all allows
- **Match:** Our 3-tier model correctly implements deny-first. Hard blocks (exit 2) take priority.

**Security** (Source: [Check Point Research - CVE-2025-59536](https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/))
- `.claude/settings.json` hooks in untrusted repos = RCE vector
- Containerization, `allowManagedHooksOnly`, `disableAllHooks` for untrusted repos
- **Gap:** Our shell injection vulnerability in `session_start.py` demonstrates the exact risk class described in CVE-2025-59536.

### Summary: Hook Architecture

| Aspect | Our System | Best Practice | Gap? |
|--------|-----------|---------------|------|
| Lifecycle coverage | 7/17 events | Context-appropriate subset | Minor |
| Handler types | command only | command + prompt + agent | **Yes** |
| Async support | None | async for non-blocking ops | **Yes** |
| Error handling | Silent swallow | Error logging + fallback | **Yes** |
| Permission model | 3-tier deny-first | Deny > Ask > Allow | Match |
| Security | Shell injection bug | shlex.quote, containerize | **Yes** |
| Performance | ~30-60ms per hook | <1ms (Rust) or <50ms | Acceptable |

---

## 2. State Machine Enforcement

### Our Approach

- 8 JSON-defined state machines (system + 7 per-agent)
- Unix socket daemon for real-time enforcement (~2ms latency)
- Write-gating by state (write_allowed, write_globs)
- Tool blocking by state (blocked_tools)
- max_occurrences on transitions (loop protection)
- think_on_exit flags
- Permissive fallback when daemon unavailable

### Industry Best Practices

**Guard Evaluation** (Source: [Stately Guards Docs](https://stately.ai/docs/guards))
- Guards must be pure, synchronous, side-effect-free
- Support composition: `and([guard1, guard2])`, `or([guard1, guard2])`, `not(guard1)`
- Parameterized guards with context-dependent evaluation
- **Critical Gap:** Our guards are never evaluated. `do_transition()` only checks from/to/trigger -- all 60+ guards are documentation only. XState, python-statemachine, and every other framework actually evaluates guards.

**Error/Recovery States** (Source: [FSMs in Self-Healing Mechanisms](https://medium.com/@kirantech2009/finite-state-machines-in-self-healing-mechanisms-and-adaptability-to-ai-1f3316e0fe95))
- Model error states explicitly in the state machine
- Recovery paths (sequences of actions) to return to correct states
- Circuit breaker pattern (Closed/Open/Half-Open) for failing agents
- **Gap:** None of our 8 machines have ERROR/FAILED states. Agents that encounter unrecoverable failures have no valid exit transition.

**Statechart Features** (Source: [Stately: State Machines and Statecharts](https://stately.ai/docs/state-machines-and-statecharts))
- Hierarchical states reduce state explosion
- Parallel regions for concurrent execution
- History states for recovery from interruptions
- Delayed transitions for timeouts
- **Gap:** Our machines are flat FSMs. The system machine has 19 states -- approaching the complexity threshold where flat FSMs become unwieldy. Parallel regions would model concurrent coder execution naturally.

**Testing** (Source: [Abstracta: Model-Based Testing](https://abstracta.us/blog/software-testing/model-based-testing-using-state-machines/))
- State coverage, transition coverage, transition-pair coverage
- Property-based testing with state machines (random event sequences)
- Use the state machine as the test oracle
- **Gap:** No state machine tests exist. The state machines are untested.

**Comparison with LangGraph** (Source: [LangGraph State Machines](https://dev.to/jamesli/langgraph-state-machines-managing-complex-agent-task-flows-in-production-36f4))
- LangGraph uses conditional edges (router functions) -- evaluated at runtime
- Checkpointing with Postgres/Redis for durability
- Human-in-the-loop at any checkpoint
- **Our advantage:** JSON-declarative state machines are more inspectable and LLM-readable than code-based definitions (validated by [Flatagents](https://github.com/memgrafter/flatagents) approach).

**Comparison with Temporal** (Source: [Temporal: Beyond State Machines](https://temporal.io/blog/temporal-replaces-state-machines-for-distributed-applications))
- Workflow-as-code with automatic checkpointing
- State is implicit in workflow function, not separate object
- Built-in retry, timeout, durable execution
- **Our advantage:** Hook-based enforcement is unique to CLI agent systems. Temporal is for server-side workflows.

### Summary: State Machine Enforcement

| Aspect | Our System | Best Practice | Gap? |
|--------|-----------|---------------|------|
| Declarative definitions | JSON files | JSON/YAML declarative | Match |
| Runtime enforcement | Socket daemon | Guard evaluation | **Critical** |
| Guard evaluation | Not implemented | Pure, synchronous, composable | **Critical** |
| Error states | None | Explicit ERROR/FAILED states | **Yes** |
| Timeout transitions | None | Delayed transitions | **Yes** |
| Hierarchy/parallel | Flat FSM | Statecharts for >15 states | **Yes** |
| Testing | None | Property-based + coverage | **Yes** |
| Write-gating | Implemented | Novel contribution | **Ahead** |
| Tool blocking | Implemented | Novel contribution | **Ahead** |
| Loop protection | max_occurrences | Standard pattern | Match |

---

## 3. Schema & Data Architecture

### Our Approach

- 12 Pydantic v2 models in `schemas/`
- W3C Trace Context fields on most models
- No `model_config` (no `extra="forbid"`)
- No custom validators
- JSON Schema files referenced in CLAUDE.md but Pydantic models used instead
- 13 domain schemas not yet written

### Industry Best Practices

**Pydantic ConfigDict** (Source: [Pydantic Performance Docs](https://docs.pydantic.dev/latest/concepts/performance/))
- `extra="forbid"` prevents silent data corruption from typo keys
- `model_construct()` for trusted internal transfers (skip validation)
- `cache_strings='keys'` or `'none'` for performance
- **Gap:** Zero `model_config` anywhere. Typo keys silently pass. This is the single highest-impact schema fix.

**Schema Versioning** (Source: [Snowplow SchemaVer](https://snowplow.io/blog/introducing-schemaver-for-semantic-versioning-of-schemas))
- SchemaVer (MODEL-REVISION-ADDITION) for data schemas
- Different from SemVer: focuses on data backward-compatibility
- Include `schemaVersion` field in every payload
- **Gap:** No schema versioning. No `schemaVersion` field on any model.

**Inter-Agent Data Contracts** (Source: [GitHub Blog: Multi-Agent Workflows](https://github.blog/ai-and-ml/generative-ai/multi-agent-workflows-often-fail-heres-how-to-engineer-ones-that-dont/))
- Typed handoffs with machine-checkable schemas
- Explicit state contracts versioned at every boundary
- Task-level evals before propagation
- Transactional rollback on validation failure
- **Gap:** Our schemas define types but don't enforce contracts at boundaries. No rollback mechanism.

**Token-Efficient Serialization** (Source: [TOON vs JSON - Tensorlake](https://www.tensorlake.ai/blog/toon-vs-json))
- TOON format: 30-60% fewer tokens than JSON
- Accuracy actually improved: 73.9% (TOON) vs 69.7% (JSON)
- Dual-schema: verbose for validation, compact for LLM context
- **Gap:** All schemas serialize to standard verbose JSON. No compact serialization for LLM context injection.

**Logging: JSONL vs SQLite** (Source: [AgentTrace - arxiv](https://arxiv.org/html/2602.10133))
- Three trace surfaces: operational, cognitive, contextual
- JSONL for real-time append; SQLite for cross-session queries
- Hybrid approach recommended
- **Match:** Our logging already uses JSONL for append operations. Missing: SQLite for aggregation.

### Summary: Schema & Data Architecture

| Aspect | Our System | Best Practice | Gap? |
|--------|-----------|---------------|------|
| Type system | Pydantic v2 BaseModel | Pydantic v2 with ConfigDict | **Yes** |
| `extra="forbid"` | Absent | On all models | **Yes** |
| Custom validators | None | Cross-field validation | **Yes** |
| Schema versioning | None | SchemaVer per payload | **Yes** |
| Token efficiency | Standard JSON | Compact/TOON for LLM context | **Yes** |
| Tracing | W3C on most models | W3C Trace Context | Match |
| Logging format | JSONL | JSONL + SQLite hybrid | Partial |
| Inter-agent contracts | Defined but not enforced | Validated at boundaries | **Yes** |

---

## 4. Design Document Coverage

### Completeness by Layer

| Layer | Designed Items | Built | Score |
|-------|---------------|-------|-------|
| **Infrastructure** (schemas, state machines, hooks, scripts, rules) | ~50 | ~40 | **~80%** |
| **Behavioral** (agent prompts, skills, anti-rationalization) | ~25 | 0 | **0%** |
| **Tooling** (TUI viewers, embedding model, PTC server) | ~5 | 0 | **0%** |
| **Data** (domain schemas for plans, context, research, audit) | 13 | 0 | **0%** |
| **Weighted Overall** | | | **~40%** |

The infrastructure is genuinely well-built. But the system cannot run a workflow because the behavioral layer (agent prompts) and data layer (domain schemas) are entirely absent.

---

## 5. Novel Contributions (Ahead of Industry)

Our system has several patterns not widely documented elsewhere:

1. **3-tier enforcement model** (hard block / ask user / state machine gated) -- unique to this project
2. **Write-gating by state** with glob patterns -- not found in LangGraph, Temporal, or XState
3. **Tool blocking by state** -- state-dependent tool availability
4. **think_on_exit** flags -- forcing extended reasoning at critical state boundaries
5. **W3C Trace Context** on Pydantic models -- deeper tracing than most agentic frameworks
6. **PTC future-proofing stubs** in hooks -- forward-compatible design for token-saving sandbox
7. **User correction detection** in PostToolUse -- learning from human edits

---

## All Sources Cited

### Claude Code Hooks
- [Official Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Official Hooks Guide](https://code.claude.com/docs/en/hooks-guide)
- [Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)
- [Blake Crosley - Why Each of My 95 Hooks Exists](https://blakecrosley.com/en/blog/claude-code-hooks)
- [Check Point Research - CVE-2025-59536](https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/)
- [JP Caparas - Async Hooks (Dev Genius)](https://blog.devgenius.io/claude-code-async-hooks-what-they-are-and-when-to-use-them-61b21cd71aad)
- [DataCamp - Claude Code Hooks Tutorial](https://www.datacamp.com/tutorial/claude-code-hooks)
- [Steve Kinney - Hook Control Flow](https://stevekinney.com/courses/ai-development/claude-code-hook-control-flow)

### State Machine Patterns
- [Stately: Guards](https://stately.ai/docs/guards)
- [Stately: State Machines and Statecharts](https://stately.ai/docs/state-machines-and-statecharts)
- [LangGraph State Machines](https://dev.to/jamesli/langgraph-state-machines-managing-complex-agent-task-flows-in-production-36f4)
- [Temporal: Beyond State Machines](https://temporal.io/blog/temporal-replaces-state-machines-for-distributed-applications)
- [Flatagents GitHub](https://github.com/memgrafter/flatagents)
- [FSMs in Self-Healing Mechanisms](https://medium.com/@kirantech2009/finite-state-machines-in-self-healing-mechanisms-and-adaptability-to-ai-1f3316e0fe95)
- [Abstracta: Model-Based Testing](https://abstracta.us/blog/software-testing/model-based-testing-using-state-machines/)
- [python-statemachine: Conditions and Validators](https://python-statemachine.readthedocs.io/en/latest/guards.html)

### Schema & Data Architecture
- [Pydantic Performance Documentation](https://docs.pydantic.dev/latest/concepts/performance/)
- [Snowplow SchemaVer](https://snowplow.io/blog/introducing-schemaver-for-semantic-versioning-of-schemas)
- [GitHub: Multi-Agent Workflows Often Fail](https://github.blog/ai-and-ml/generative-ai/multi-agent-workflows-often-fail-heres-how-to-engineer-ones-that-dont/)
- [TOON vs JSON (Tensorlake)](https://www.tensorlake.ai/blog/toon-vs-json)
- [AgentTrace (arxiv)](https://arxiv.org/html/2602.10133)
- [msgspec Official Benchmarks](https://jcristharif.com/msgspec/benchmarks.html)

### Agentic AI Patterns
- [Confluent: Event-Driven Multi-Agent Systems](https://www.confluent.io/blog/event-driven-multi-agent-systems/)
- [A2A Protocol Specification](https://a2a-protocol.org/latest/specification/)
- [Securing Agentic AI (Anthropic)](https://platform.claude.com/docs/en/agent-sdk/secure-deployment)
- [AWS: Agentic AI Security Scoping Matrix](https://aws.amazon.com/blogs/security/the-agentic-ai-security-scoping-matrix-a-framework-for-securing-autonomous-ai-systems/)
