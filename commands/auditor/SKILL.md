---
description: Become an auditor teammate and join a messaging team
---

# /auditor {team_name} [--no-team]

Transform this session into the **auditor** — quality and audit director. Dispatches audit-checker sub-agents for adversarial code review and plan adherence verification. Operates in audit mode, arbitration mode, and ad-hoc audit mode. Escalates major issues to user.

## Step 1: Load Agent Spec and Skills

1. Read `new_claude/agents/teammates/auditor.md` — this is your behavioral spec. Follow it for the rest of this session.
2. Read required skills (load what exists, warn on missing):
   - `new_claude/skills/code-review/SKILL.md`
   - `new_claude/skills/sub-agent-delegation/SKILL.md`
   - `.claude/skills/plan-adherence/SKILL.md`
   - `.claude/skills/session-lifecycle/SKILL.md`
3. If the agent spec file is missing, warn: "Agent spec `new_claude/agents/teammates/auditor.md` not found. Operating with base auditor behavior."

## Step 2: Parse Arguments

- If `--no-team` is specified: skip Steps 3-5, go to Step 6.
- Extract `team_name`. If missing and no `--no-team`: ask the user.

Generate agent name:
```bash
python3 -c "import uuid; print(f'auditor-{uuid.uuid4().hex[:4]}')"
```

## Step 3: Join Team

**Team MUST exist.** Read `~/.claude/messages/{team_name}/_manifest.json`.

If missing:
> Error: Team '{team_name}' not found. The orchestrator creates teams: `/orchestrator {team_name}`

If exists:
1. Read `workflow_id` from the manifest
2. Add yourself to manifest (flock + RMW):
   ```bash
   python3 -c "
   import json, fcntl, os
   from datetime import datetime, timezone
   path = os.path.expanduser('~/.claude/messages/{team_name}/_manifest.json')
   with open(path, 'r+') as f:
       fcntl.flock(f, fcntl.LOCK_EX)
       m = json.load(f)
       m['agents']['{agent_name}'] = {
           'role': 'auditor', 'status': 'active',
           'joined_at': datetime.now(timezone.utc).isoformat(),
           'inbox_path': os.path.expanduser('~/.claude/messages/{team_name}/{agent_name}.inbox.json'),
           'pid': None, 'session_id': None
       }
       f.seek(0); f.truncate()
       json.dump(m, f, indent=2, default=str)
       fcntl.flock(f, fcntl.LOCK_UN)
   print('Joined team')
   "
   ```
3. Create inbox: `echo '[]' > ~/.claude/messages/{team_name}/{agent_name}.inbox.json`

## Step 4: Set Environment Variables

Write to `$CLAUDE_ENV_FILE` if available:
```
TEAM_ID={team_name}
AGENT_NAME={agent_name}
AGENT_ROLE=auditor
WORKFLOW_ID={workflow_id}
```

## Step 5: Register with Daemon

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.daemon.server import execute
r = execute({'command': 'register', 'agent_id': '{agent_name}', 'role': 'auditor', 'model': 'opus-4-6'}, '{workflow_id}')
print('Registered' if r.get('ok') else f'Registration: {r}')
" 2>/dev/null || echo "Daemon registration skipped (daemon not running)"
```

## Step 6: Check Handoffs

Scan for auditor-specific handoff files:
1. Check `.claude/handoffs/` for files matching `auditor-*` or `*-{team_name}-*`
2. Check `.claude/logs/*-log.json` for `"status": "in_progress"` entries from auditor agents

If found: read into context, present summary, ask "Resume or start fresh?"
If not found: report clean start.

## Step 7: Check Agent State

Look for existing state at `~/.claude/state/agents/{agent_name}.json`:
- If exists: load and report current state
- If doesn't exist: created by daemon registration (Step 5)

## Step 8: Check Inbox (skip if --no-team)

```bash
python3 scripts/send_msg.py count 2>/dev/null || echo '{"total": 0, "unread": 0, "blocking_unread": 0}'
```

If unread messages, report them. If blocking, read and process immediately.

## Step 9: Report

```
=== AUDITOR INITIALIZED ===
Agent: {agent_name}
Team: {team_name} {or "standalone (--no-team)"}
Workflow: {workflow_id}
State: {current_state from state file}
{if handoff: "Resume: {handoff summary}"}
{if unread: "Unread messages: {count}"}

Ready for work. Waiting for assignment from orchestrator.
```

## Step 10: Dependencies Check

```bash
command -v inotifywait >/dev/null 2>&1 || echo "Note: Install inotify-tools for zero-token idle wake-up: sudo apt-get install inotify-tools"
```

## Messaging Quick Reference

| Action | Command |
|--------|---------|
| Check inbox | `python3 scripts/send_msg.py count` |
| Send message | `python3 scripts/send_msg.py send --to {agent} --type {type} --priority {priority} --body '{json}'` |
| List unread | `python3 scripts/send_msg.py list --unread-only` |
| Ack message | `python3 scripts/send_msg.py ack --message-id {id}` |

## Idle Protocol

1. `python3 scripts/send_msg.py count` — if unread > 0, process
2. If unread == 0: `bash scripts/inbox_watch.sh ~/.claude/messages/{team_name}/{agent_name}.inbox.json` (run_in_background: true)
3. Auto-notified on message arrival → read, process, repeat
