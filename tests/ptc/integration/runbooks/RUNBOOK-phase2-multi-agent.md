# Phase 2: Multi-Agent Isolation and Concurrency

## Prerequisites

- [ ] Phase 1 runbook completed successfully
- [ ] Docker daemon running with sufficient resources (recommend 8 GB+ RAM free)
- [ ] PTC sandbox image built (`docker image inspect ptc-sandbox:latest`)
- [ ] No leftover PTC containers (`docker ps -a --filter name=ptc- -q` returns empty)
- [ ] System monitoring available (`top`, `htop`, or `docker stats`)

## Step-by-Step Test Sequence

### Step 1: Multi-Agent Isolation

```bash
pytest tests/ptc/integration/test_phase2_multi_agent_isolation.py -v --timeout=300
```

**Expected:** Two agents with different roles get separate containers. File writes in one container are invisible to the other. pip installs in one do not affect the other.

**Manual verification:**
```bash
# List running PTC containers during test
docker ps --filter name=ptc- --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"

# Verify filesystem isolation
docker exec <CONTAINER_1> ls /tmp/workspace/
docker exec <CONTAINER_2> ls /tmp/workspace/
# Should show different contents
```

### Step 2: Concurrent Execution

```bash
pytest tests/ptc/integration/test_phase2_concurrent_execution.py -v --timeout=300
```

**Expected:** 4 containers created concurrently. All execute code simultaneously without blocking. Execution time for 4 concurrent tasks is less than 4x sequential time.

**Manual verification:**
```bash
# Watch containers come up in real-time
watch -n 0.5 'docker ps --filter name=ptc- --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"'
```

### Step 3: Resource Contention

```bash
pytest tests/ptc/integration/test_phase2_resource_contention.py -v --timeout=300
```

**Expected:** 4 containers running pip install simultaneously do not OOM the host. Each container stays within its memory limit. CPU is shared fairly.

**Manual verification:**
```bash
# Monitor resource usage during concurrent pip installs
docker stats --filter name=ptc- --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"

# Check host-level memory
free -h
```

### Step 4: Role Diversity

```bash
pytest tests/ptc/integration/test_phase2_role_diversity.py -v --timeout=300
```

**Expected:** Agents with different role configs (data-analyst, researcher, coder) get appropriate package sets. Role-specific tools are available only in the correct container.

## Data Collection

| Metric | Value | Notes |
|--------|-------|-------|
| 4-container total memory | ___ MB | Sum of all container memory |
| Per-container average memory | ___ MB | Total / 4 |
| Peak CPU during pip install (4x) | ___ % | Host CPU during concurrent installs |
| Concurrent creation time (4x) | ___ s | Time to create all 4 containers |
| Sequential creation time (4x) | ___ s | Time if created one-by-one |
| Concurrent execution time ratio | ___ | concurrent / sequential (should be < 1.5) |
| Host free memory during test | ___ MB | `free -m` available column |

```bash
# Snapshot all container resources
docker stats --no-stream --filter name=ptc- --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.PIDs}}" > phase2_resources.csv

# Host memory snapshot
free -m | grep Mem | awk '{print "Total:",$2,"MB Used:",$3,"MB Free:",$4,"MB"}'
```

## Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| All pytest tests pass | 0 failures | Any failure |
| Filesystem isolation | No cross-container file leaks | Shared state detected |
| 4-container total memory | < 800 MB | > 1.5 GB |
| Concurrent creation time | < 15 s for 4 containers | > 30 s |
| No OOM kills | All containers healthy | Any OOM kill |
| Host memory stable | Free memory > 2 GB | Free memory < 500 MB |
| No zombie containers after tests | `docker ps -a --filter name=ptc-` empty | Leftover containers |
