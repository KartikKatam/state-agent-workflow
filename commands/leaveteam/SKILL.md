---
description: Leave the current messaging team
---

# /leaveteam

Leave your current messaging team gracefully.

## Steps

1. **Detect current team**: Read `TEAM_ID` and `AGENT_NAME` from environment. If not set, scan `~/.claude/messages/` for manifests containing an agent matching your session.

2. **Update manifest**: Set your status to "departed" in the team manifest (flock + RMW).

3. **Preserve inbox**: Do NOT delete the inbox file — it contains message history for debugging.

4. **Clear environment**: Unset TEAM_ID, AGENT_NAME, AGENT_ROLE env vars.

5. **Report**:
   ```
   Left team "{team_name}". Inbox preserved at ~/.claude/messages/{team_name}/{agent_name}.inbox.json
   ```
