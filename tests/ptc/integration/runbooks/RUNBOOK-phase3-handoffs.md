# Phase 3: Agent Handoffs and Container Persistence

## Prerequisites

- [ ] Phases 1–2 runbooks completed successfully
- [ ] Docker daemon running
- [ ] PTC sandbox image built (`docker image inspect ptc-sandbox:latest`)
- [ ] No leftover PTC containers (`docker ps -a --filter name=ptc- -q` returns empty)
- [ ] Understand handoff concept: new agent inherits same container, gets fresh Python namespace, retains installed packages

## Step-by-Step Test Sequence

### Step 1: Handoff Lifecycle

```bash
pytest tests/ptc/integration/test_phase3_handoff_lifecycle.py -v --timeout=120
```

**Expected:** Agent A creates container and installs packages. Agent B receives handoff — same container ID, fresh namespace, packages still available. Handoff is faster than creating a new container.

**Manual verification:**
```bash
# Before handoff — note the container ID
docker ps --filter name=ptc- --format "{{.ID}} {{.Names}}"

# After handoff — container ID should be the SAME
docker ps --filter name=ptc- --format "{{.ID}} {{.Names}}"
```

### Step 2: Namespace Reset

```bash
pytest tests/ptc/integration/test_phase3_namespace_reset.py -v --timeout=120
```

**Expected:** Variables defined by Agent A are not visible to Agent B after handoff. Imports work fresh. Global state is clean.

**Manual verification:**
```bash
# Agent A defines variable
docker exec <CONTAINER_ID> python3 -c "x = 42; print(x)"

# After namespace reset, x should not exist
docker exec <CONTAINER_ID> python3 -c "print(x)"
# Should raise NameError
```

### Step 3: Container Persistence

```bash
pytest tests/ptc/integration/test_phase3_container_persistence.py -v --timeout=120
```

**Expected:** Container filesystem survives handoff. Files written by Agent A are readable by Agent B. pip-installed packages remain importable.

**Manual verification:**
```bash
# Agent A installs a package
docker exec <CONTAINER_ID> pip install requests

# After handoff, Agent B can import it
docker exec <CONTAINER_ID> python3 -c "import requests; print(requests.__version__)"
# Should succeed

# Agent A writes a file
docker exec <CONTAINER_ID> python3 -c "open('/tmp/workspace/test.txt','w').write('from A')"

# After handoff, Agent B reads it
docker exec <CONTAINER_ID> python3 -c "print(open('/tmp/workspace/test.txt').read())"
# Should print "from A"
```

### Step 4: Idle Cleanup

```bash
pytest tests/ptc/integration/test_phase3_idle_cleanup.py -v --timeout=300
```

**Expected:** Containers that exceed idle timeout are cleaned up automatically. Active containers are not affected. Cleanup is logged.

**Manual verification:**
```bash
# Watch for container removal after idle timeout
watch -n 5 'docker ps -a --filter name=ptc- --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.RunningFor}}"'
```

## Data Collection

| Metric | Value | Notes |
|--------|-------|-------|
| Handoff time | ___ s | Time from handoff call to new agent ready |
| Fresh container creation time | ___ s | For comparison with handoff time |
| Speedup ratio | ___ x | creation_time / handoff_time |
| Container ID before handoff | ___ | `docker ps` output |
| Container ID after handoff | ___ | Should match above |
| Namespace reset confirmed | Y / N | Agent B cannot see Agent A's variables |
| Packages survive handoff | Y / N | Agent B can import Agent A's packages |
| Files survive handoff | Y / N | Agent B can read Agent A's files |
| Idle cleanup timeout | ___ s | Time before idle container removed |

```bash
# Compare handoff vs creation timing
echo "Handoff time: $(pytest tests/ptc/integration/test_phase3_handoff_lifecycle.py -v 2>&1 | grep -oP 'handoff_time=\K[\d.]+')"
echo "Creation time: $(pytest tests/ptc/integration/test_phase1_container_lifecycle.py -v 2>&1 | grep -oP 'creation_time=\K[\d.]+')"
```

## Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| All pytest tests pass | 0 failures | Any failure |
| Container ID persists | Same ID before/after handoff | Different container IDs |
| Namespace is fresh | Agent B cannot access Agent A variables | Variable leakage |
| Packages survive | Agent B imports Agent A packages | ImportError |
| Files survive | Agent B reads Agent A files | FileNotFoundError |
| Handoff faster than create | handoff_time < creation_time | Handoff slower |
| Idle cleanup works | Container removed after timeout | Container persists indefinitely |
| No zombie containers | Clean state after all tests | Leftover containers |
