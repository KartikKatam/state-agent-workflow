---
description: Become the orchestrator and join or create a messaging team
---

# /orchestrator {team_name}

Transform this session into the **orchestrator** — the workflow coordinator that spawns and manages teammates, enforces workflow phases, and interfaces with the user. Never writes code directly.

## Step 1: Load Agent Spec and Skills

1. Read `new_claude/agents/teammates/orchestrator.md` — this is your behavioral spec. Follow it for the rest of this session.
2. Read required skills:
   - `new_claude/skills/workflow-coordination/SKILL.md` (if it exists)
   - `new_claude/skills/sub-agent-delegation/SKILL.md`
   - `new_claude/skills/delegation-prompts/SKILL.md`
   - `.claude/skills/session-lifecycle/SKILL.md`
3. If the agent spec file is missing, warn the user: "Agent spec `new_claude/agents/teammates/orchestrator.md` not found. Operating with base orchestrator behavior."

## Step 2: Parse Arguments

Extract `team_name` from the command arguments. If not provided, ask the user.

Generate identifiers:
```bash
python3 -c "import uuid; suffix = uuid.uuid4().hex[:4]; print(f'orchestrator-{suffix}')"
```
- `agent_name`: use the output (e.g., `orchestrator-a7f2`)
- `workflow_id`: will be read from existing manifest or generated as `{team_name}-{8 hex}`

## Step 3: Team Join or Create

Check if `~/.claude/messages/{team_name}/_manifest.json` exists.

### If team EXISTS → Join

1. Read the manifest
2. Check for existing active orchestrator — if found, warn user and ask to take over or use a different name
3. Read `workflow_id` from the manifest (do NOT generate a new one)
4. Add yourself to the manifest via flock + RMW:
   ```bash
   python3 -c "
   import json, fcntl, os
   from datetime import datetime, timezone
   path = os.path.expanduser('~/.claude/messages/{team_name}/_manifest.json')
   with open(path, 'r+') as f:
       fcntl.flock(f, fcntl.LOCK_EX)
       m = json.load(f)
       m['agents']['{agent_name}'] = {
           'role': 'orchestrator', 'status': 'active',
           'joined_at': datetime.now(timezone.utc).isoformat(),
           'inbox_path': os.path.expanduser('~/.claude/messages/{team_name}/{agent_name}.inbox.json'),
           'pid': None, 'session_id': None
       }
       f.seek(0); f.truncate()
       json.dump(m, f, indent=2, default=str)
       fcntl.flock(f, fcntl.LOCK_UN)
   print('Joined existing team')
   "
   ```
5. Create inbox if missing: `[ -f ~/.claude/messages/{team_name}/{agent_name}.inbox.json ] || echo '[]' > ~/.claude/messages/{team_name}/{agent_name}.inbox.json`

### If team DOES NOT EXIST → Create

1. Generate workflow_id:
   ```bash
   python3 -c "import uuid; print(f'{team_name}-' + uuid.uuid4().hex[:8])"
   ```
2. Create directory + manifest + inbox + registry:
   ```bash
   python3 -c "
   import json, fcntl, os
   from datetime import datetime, timezone
   team = '{team_name}'
   wf_id = '{workflow_id}'
   agent = '{agent_name}'
   now = datetime.now(timezone.utc).isoformat()
   msg_root = os.path.expanduser('~/.claude/messages')
   team_dir = os.path.join(msg_root, team)
   os.makedirs(team_dir, exist_ok=True)

   # Manifest
   manifest = {
       'team_id': team, 'workflow_id': wf_id,
       'created_at': now, 'created_by': agent,
       'agents': {
           agent: {
               'role': 'orchestrator', 'status': 'active',
               'joined_at': now, 'pid': None, 'session_id': None,
               'inbox_path': os.path.join(team_dir, f'{agent}.inbox.json')
           }
       }
   }
   with open(os.path.join(team_dir, '_manifest.json'), 'w') as f:
       json.dump(manifest, f, indent=2, default=str)

   # Inbox
   with open(os.path.join(team_dir, f'{agent}.inbox.json'), 'w') as f:
       json.dump([], f)

   # Registry
   reg_path = os.path.join(msg_root, '_registry.json')
   if os.path.exists(reg_path):
       with open(reg_path, 'r+') as f:
           fcntl.flock(f, fcntl.LOCK_EX)
           reg = json.load(f)
           reg.setdefault('teams', {})[team] = {'workflow_id': wf_id, 'created_at': now, 'status': 'active'}
           f.seek(0); f.truncate()
           json.dump(reg, f, indent=2, default=str)
           fcntl.flock(f, fcntl.LOCK_UN)
   else:
       with open(reg_path, 'w') as f:
           json.dump({'teams': {team: {'workflow_id': wf_id, 'created_at': now, 'status': 'active'}}}, f, indent=2, default=str)

   print(f'Team {team} created. Workflow: {wf_id}')
   "
   ```
3. Start daemon if not running:
   ```bash
   python3 -c "
   from scripts.daemon.server import daemon_status, send_to_daemon
   status = daemon_status('{workflow_id}')
   print('Daemon running' if status.get('running') else 'Daemon not running — start with: python3 scripts/daemon/server.py serve --workflow-id {workflow_id}')
   " 2>/dev/null || echo "Daemon check failed — start manually: python3 scripts/daemon/server.py serve --workflow-id {workflow_id} &"
   ```

## Step 4: Set Environment Variables

Write to `$CLAUDE_ENV_FILE` if available:
```
TEAM_ID={team_name}
AGENT_NAME={agent_name}
AGENT_ROLE=orchestrator
WORKFLOW_ID={workflow_id}
```

## Step 5: Register with Daemon

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from scripts.daemon.server import execute
r = execute({'command': 'register', 'agent_id': '{agent_name}', 'role': 'orchestrator', 'model': 'opus-4-6'}, '{workflow_id}')
print('Registered' if r.get('ok') else f'Registration: {r}')
" 2>/dev/null || echo "Daemon registration skipped (daemon not running)"
```

## Step 6: Check Handoffs

Scan for orchestrator-specific resume state:
1. Check `.claude/handoffs/` for files matching `orchestrator-*` or `*-{team_name}-*`
2. Check `.claude/temp/orchestrator-state.json` for saved state
3. Check `.claude/logs/*-log.json` for `"status": "in_progress"` entries

If found: read the handoff into context, present summary, ask "Resume or start fresh?"
If not found: report clean start.

## Step 7: Check Inbox

```bash
python3 scripts/send_msg.py count 2>/dev/null || echo '{"total": 0, "unread": 0, "blocking_unread": 0}'
```

If unread messages exist, read and process them.

## Step 8: Report

```
=== ORCHESTRATOR INITIALIZED ===
Agent: {agent_name}
Team: {team_name} (JOINED existing / CREATED new)
Workflow: {workflow_id}
Inbox: ~/.claude/messages/{team_name}/{agent_name}.inbox.json
{if handoff found: "Resume: {handoff summary}"}
{if unread: "Unread messages: {count}"}

Team members: {list from manifest}
Teammates join with: /{role} {team_name}
```

## Step 9: Dependencies Check

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
