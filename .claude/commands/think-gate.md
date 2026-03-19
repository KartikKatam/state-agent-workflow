# Think Gate

Set or clear a deliberation checkpoint. When active, the think-gate hook blocks all mutations (Write, Edit, Bash) until the agent calls the think tool addressing the prompt you provide.

## Instructions

**If the user provided a prompt (e.g., `/think-gate What edge cases could break?`):**

1. Write the following JSON to `~/.claude/think-gate.json` (create or overwrite):
```json
{
  "prompt": "<the user's prompt exactly as given>",
  "created_at": "<current UTC ISO timestamp>"
}
```
2. Confirm to the user: "Think gate active. I'll need to call the think tool addressing your prompt before making any file changes."
3. Then immediately call the think tool with your reasoning about the prompt before continuing with any implementation work.

**If the user said `/think-gate clear`:**

1. Delete `~/.claude/think-gate.json` if it exists.
2. Confirm: "Think gate cleared."

**If the user said `/think-gate` with no arguments:**

1. Check if `~/.claude/think-gate.json` exists.
2. If yes, read it and report the active prompt and when it was set.
3. If no, report "No think gate active."

## Notes

- The think-gate PreToolUse hook must be enabled in `~/.claude/settings.json` for enforcement to work. Without it, this skill sets the marker but nothing blocks mutations.
- The think tool logs full thought content to `~/.claude/logs/decisions/` for traceability.
- The gate auto-clears after one think call. For persistent gating, use the daemon's state machine `think_on_exit` instead.
