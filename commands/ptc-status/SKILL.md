# PTC Status Dashboard

Show the current state of the PTC (Process/Test Container) sandbox system.

## Instructions

1. Check if the PTC MCP server process is running:
   ```bash
   pgrep -f "ptc-server/server.py" || echo "PTC server not running"
   ```

2. List active Docker containers:
   ```bash
   docker ps --filter "name=ptc" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Size}}" 2>/dev/null || echo "Docker not available"
   ```

3. Check REPL pool status — read the PTC session ID and check for active sockets:
   ```bash
   cat ~/.claude/.ptc-session-id 2>/dev/null && ls /tmp/ptc-ipc/ 2>/dev/null || echo "No active IPC sockets"
   ```

4. Show recent PTC event log (last 10 entries):
   ```bash
   tail -10 ~/.claude/logs/ptc-events.jsonl 2>/dev/null || echo "No PTC event log found"
   ```

5. Show resource config:
   ```bash
   python3 -c "import json; c=json.load(open(os.path.expanduser('~/.claude/mcp/ptc-server/config.json'))); print(json.dumps(c['resource_limits'], indent=2))" 2>/dev/null
   ```

6. Present a concise dashboard to the user:
   - Server: running/stopped (PID if running)
   - Containers: count, names, uptime
   - IPC sockets: count, paths
   - Recent activity: last 3-5 events summarized
   - Resource limits: memory, CPU, timeout, max containers

Keep output compact. Use a table or structured format. Do not over-explain.
