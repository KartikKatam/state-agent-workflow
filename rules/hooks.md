---
globs: ["hooks/**/*.py"]
---

# Hook Development Rules

## Architecture

Hooks are Python scripts that fire at specific points in the Claude Code lifecycle. They run in a subprocess — not in the agent's context. They must be fast, deterministic, and side-effect-aware.

### Hook Types

| Hook | Fires | Stdin | Key Outputs |
|------|-------|-------|-------------|
| `PreToolUse` | Before tool call | `{tool_name, tool_input}` | `permissionDecision: allow/deny/ask`, exit 2 = hard block |
| `PostToolUse` | After tool call | `{tool_name, tool_input, tool_result}` | `additionalContext`, logging side effects |
| `SessionStart` | Session begins | session info | Agent registration, preferences loading |
| `PreCompact` | Before context compression | conversation state | State saving for recovery |
| `Stop` | Agent termination | session info | Lightweight completion validation |
| `SubagentStop` | Sub-agent finishes | sub-agent result | Result validation, `decision: block` to continue |

## Performance Requirements

- **PreToolUse**: MUST complete in <50ms. No Pydantic imports, no heavy I/O. Use raw socket to workflow_state daemon.
- **PostToolUse**: 10s timeout. Can do heavier work (schema validation, logging, annotation checks).
- **SessionStart**: Runs once — can be slower (agent registration, file reads).

## Rules

- Never import Pydantic in PreToolUse — it adds ~200ms cold start
- Always use `sys.path.insert(0, ...)` at the top to find project modules
- Read environment variables for agent identity: `CLAUDE_CODE_AGENT_NAME`, `AGENT_ROLE`, `WORKFLOW_ID`
- Exit codes matter: `exit(0)` = success, `exit(2)` = hard block (PreToolUse only), any other non-zero = error (logged but not blocking)
- Stdout is parsed as JSON by Claude Code — malformed output silently fails
- Stderr goes to hook logs — use it for debugging
- Hooks run in the repo root directory

## Output Schema

Hook JSON output follows `schemas/hook_output.py`:
- `hookSpecificOutput.permissionDecision` — for PreToolUse
- `hookSpecificOutput.additionalContext` — inject text into agent context (PostToolUse)
- `hookSpecificOutput.decision` — for SubagentStop (`block` or `allow`)

## Common Patterns

- **Dispatch table**: PostToolUse uses `{tool_name: handler}` dict for O(1) routing
- **Socket client**: PreToolUse queries workflow_state daemon via Unix domain socket
- **Event logging**: Use `hooks/utils/event_logger.py` for consistent JSONL logging with trace context
- **Schema validation**: Use `hooks/utils/schema_validator.py` for validating .claude/ JSON files

## Testing Hooks

Hooks are tested by piping JSON to stdin:
```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"test.py"}}' | python3 hooks/pre_tool_use.py
```

## Avoiding Common Mistakes

- Do NOT modify files that agents are editing — hooks are observers, not actors (except for state files and logs)
- Do NOT block on network calls in PreToolUse — timeout will cause silent failure
- Do NOT assume agent state exists — always handle missing/corrupt state files gracefully
- Do NOT log to agent-visible files from hooks — use `~/.claude/logs/` for hook-internal logging
