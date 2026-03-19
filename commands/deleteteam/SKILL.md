---
description: Delete a messaging team (orchestrator only)
---

# /deleteteam {team_name}

Delete a messaging team and clean up all files. Only the orchestrator can do this.

## Steps

1. **Parse arguments**: Extract `team_name`. If not provided, use current TEAM_ID from env.

2. **Verify permissions**: Read manifest, check that current agent (AGENT_NAME env var) has role "orchestrator". If not, error: "Only the orchestrator can delete a team."

3. **Check for active agents**: Scan manifest for agents with status "active" (excluding self). If any found, warn: "Active agents still in team: {list}. They should `/leaveteam` first." Ask user to confirm forced deletion.

4. **Remove team directory**: `rm -rf ~/.claude/messages/{team_name}/`

5. **Update registry**: Remove team entry from `~/.claude/messages/_registry.json` (flock + RMW).

6. **Report**:
   ```
   Team "{team_name}" deleted.
   ```
