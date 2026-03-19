# Phase 4: Sub-Agent Routing and REPL Management

## Prerequisites

- [ ] Phases 1–3 runbooks completed successfully
- [ ] Docker daemon running with sufficient resources (recommend 8 GB+ RAM free)
- [ ] PTC sandbox image built (`docker image inspect ptc-sandbox:latest`)
- [ ] No leftover PTC containers (`docker ps -a --filter name=ptc- -q` returns empty)
- [ ] Understand sub-agent concept: multiple REPL processes within a single container, each with isolated namespaces, routed by sub-agent ID

## Step-by-Step Test Sequence

### Step 1: Sub-Agent Routing

```bash
pytest tests/ptc/integration/test_phase4_sub_agent_routing.py -v --timeout=120
```

**Expected:** Parent agent creates a container. Sub-agents are registered with unique IDs. Code execution is routed to the correct sub-agent REPL. Results return to the correct caller.

**Manual verification:**
```bash
# List REPL processes inside the container
docker exec <CONTAINER_ID> pgrep -la python3

# Each sub-agent should have its own python3 process
# Expected output (example):
#   12 python3 /repl/main.py --id=parent
#   45 python3 /repl/main.py --id=sub-001
#   78 python3 /repl/main.py --id=sub-002
```

### Step 2: REPL Isolation

```bash
pytest tests/ptc/integration/test_phase4_repl_isolation.py -v --timeout=120
```

**Expected:** Variables defined in one sub-agent REPL are not visible in another. Each sub-agent has independent import state. Side effects (file writes) are visible across REPLs (shared filesystem).

**Manual verification:**
```bash
# Sub-agent 1 defines a variable
# Sub-agent 2 should NOT see it
# But files written by sub-agent 1 should be readable by sub-agent 2
docker exec <CONTAINER_ID> ls /tmp/workspace/
```

### Step 3: REPL Limit

```bash
pytest tests/ptc/integration/test_phase4_repl_limit.py -v --timeout=120
```

**Expected:** Maximum REPL count is enforced (6 per container). Attempting to create beyond the limit raises RuntimeError with "max" in the message. Existing REPLs continue functioning.

**Manual verification:**
```bash
# Count REPL processes
docker exec <CONTAINER_ID> pgrep -c python3

# Should not exceed configured max
echo "Max REPLs: $(docker exec <CONTAINER_ID> cat /etc/ptc/config.json | python3 -c 'import sys,json; print(json.load(sys.stdin).get(\"max_repls\", \"N/A\"))')"
```

### Step 4: Concurrent Sub-Agents

```bash
pytest tests/ptc/integration/test_phase4_concurrent_sub_agents.py -v --timeout=120
```

**Expected:** Multiple sub-agents execute code simultaneously without interference. Results are correctly routed back. No deadlocks or race conditions.

**Manual verification:**
```bash
# Monitor container resource usage during concurrent execution
docker stats --no-stream --filter name=ptc- --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}"
```

### Step 5: Manual Process Verification

This step is manual — run after automated tests complete.

```bash
# 1. Create a parent agent with sub-agents via the test harness
pytest tests/ptc/integration/test_phase4_sub_agent_routing.py::test_sub_agent_in_full_container_gets_own -v --timeout=60

# 2. While test is paused (or use a debug breakpoint), inspect the container:
docker exec <CONTAINER_ID> pgrep -la python3
# Verify: one process per sub-agent + parent

# 3. Check memory per REPL
docker exec <CONTAINER_ID> ps aux --sort=-%mem | head -10
# Each python3 process should use < 50 MB

# 4. Verify PID namespace
docker exec <CONTAINER_ID> ls /proc/ | grep -E '^[0-9]+$' | wc -l
# Should be a small number (container PID namespace, not host)
```

## Data Collection

| Metric | Value | Notes |
|--------|-------|-------|
| Sub-agent creation time (no pip) | ___ s | Time to register and start new REPL |
| Sub-agent creation time (with packages) | ___ s | If sub-agent needs additional packages |
| Max REPLs per container | ___ | Configured limit |
| Actual REPLs before limit hit | ___ | Should match configured limit |
| Memory with 1 REPL | ___ MB | Container memory with parent only |
| Memory with 3 REPLs | ___ MB | Container memory with parent + 2 subs |
| Memory with 6 REPLs | ___ MB | Container memory approaching limit |
| Per-REPL memory overhead | ___ MB | (mem_6 - mem_1) / 5 |
| Concurrent execution latency | ___ ms | 3 sub-agents executing simultaneously |
| Routing accuracy | ___ % | Correct results returned to correct caller |

```bash
# Collect per-process memory inside container
docker exec <CONTAINER_ID> ps aux --sort=-%mem | grep python3 | awk '{print $6/1024, "MB", $11, $12, $13}'

# Container-level stats
docker stats --no-stream --filter name=ptc- --format "{{.Name}},{{.MemUsage}},{{.PIDs}}"
```

## Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| All pytest tests pass | 0 failures | Any failure |
| Routing correctness | 100% of results to correct caller | Any misrouted result |
| REPL isolation | No variable leakage between sub-agents | Shared state detected |
| REPL limit enforced | Error on exceeding max | Unlimited REPLs created |
| Sub-agent creation time | < 1 s (no pip) | > 3 s |
| Per-REPL memory overhead | < 50 MB | > 100 MB |
| Memory with 6 REPLs | < 500 MB | > 800 MB |
| No deadlocks | All concurrent executions complete | Any timeout/hang |
| No zombie processes | Clean `pgrep` output after cleanup | Orphan python3 processes |
