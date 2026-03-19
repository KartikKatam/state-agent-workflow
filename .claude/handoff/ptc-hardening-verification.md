# PTC MCP Hardening — Verification Handoff

## What Changed

The following files were modified in the PTC MCP Hardening implementation:

| File | Change |
|------|--------|
| `.claude/hooks/SessionStart.py` | Added `_write_ptc_session_id()` — writes session ID to `~/.claude/.ptc-session-id` after `emit("session_started")` |
| `~/.claude/mcp/ptc-server/server.py` | Added `import os`, `PTC_PROJECT_ROOT` env var override for project root, session ID fallback writer at startup |
| `~/.claude/mcp/ptc-server/config.json` | Added `mounts` section with `mode: "project_root"` default and empty `custom` array |
| `~/.claude/mcp/ptc-server/container_manager.py` | Added `_build_mount_args()` and `_build_pythonpath()` methods; replaced hardcoded `-v` mount and `PYTHONPATH` with dynamic versions |
| `/home/kartik/personal/ptc-benchmark/report.md` | Added instrumentation note for tasks 28-45 |

## Verification Tests to Run

### 1. Existing tests (regression)

```bash
pytest ~/.claude/mcp/ptc-server/tests/test_hardening.py -v
```

Expected: **34/34 pass** (already confirmed once during implementation).

### 2. Session ID wiring (manual)

```bash
# After restarting Claude Code, the hook should have written this file:
cat ~/.claude/.ptc-session-id
# Should contain a UUID matching the current session ID
```

### 3. Default mount behavior (no regression)

With `config.json` mounts set to `"mode": "project_root"` (default):

```python
# In a PTC container, verify /workspace is mounted:
import os; print(os.listdir("/workspace"))
```

### 4. Custom mount behavior

Set `~/.claude/mcp/ptc-server/config.json` mounts to:
```json
"mounts": {
    "mode": "custom",
    "custom": [
        {"host": "/home/kartik", "container": "/home/kartik", "access": "rw"}
    ]
}
```

Then restart the PTC server and verify:
```python
import os; print(os.listdir("/home/kartik"))
# Should list home directory contents
```

```python
import sys; print(sys.path)
# PYTHONPATH should include /home/kartik and /ptc_server
```

**Remember to revert config.json mounts back to `"mode": "project_root"` after testing if you want default behavior.**

### 5. PTC_PROJECT_ROOT env var

```bash
PTC_PROJECT_ROOT=/tmp pytest ~/.claude/mcp/ptc-server/tests/test_hardening.py -v
# Tests should still pass — env var only affects runtime project root detection
```

## Key Files

- Tests: `~/.claude/mcp/ptc-server/tests/test_hardening.py`
- Test fixtures: `~/.claude/mcp/ptc-server/tests/conftest.py`
- Server: `~/.claude/mcp/ptc-server/server.py`
- Container manager: `~/.claude/mcp/ptc-server/container_manager.py`
- Config: `~/.claude/mcp/ptc-server/config.json`
- Hook: `.claude/hooks/SessionStart.py`
