# Phase 1: Single Agent Container Lifecycle

## Prerequisites

- [ ] Docker daemon running (`docker info` succeeds)
- [ ] PTC sandbox image built (`docker image inspect ptc-sandbox:latest`)
- [ ] Python 3.11+ with pytest installed
- [ ] Working directory: project root
- [ ] No leftover PTC containers (`docker ps -a --filter name=ptc- -q` returns empty)

## Step-by-Step Test Sequence

### Step 1: Verify or Build Image

```bash
# Check if image exists
docker image inspect ptc-sandbox:latest > /dev/null 2>&1 && echo "Image ready" || echo "Image missing"

# If missing, build it
./build-image.sh

# Verify image layers
docker history ptc-sandbox:latest --no-trunc
```

**Expected:** Image exists with Python 3.11+, pip, and base packages pre-installed.

### Step 2: Container Creation and Lifecycle

```bash
pytest tests/ptc/integration/test_phase1_container_lifecycle.py -v --timeout=120
```

**Expected:** All tests pass. Container creates, starts, and stops cleanly. Container ID is returned.

**Manual verification:**
```bash
# During test, in another terminal:
docker ps --filter name=ptc- --format "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### Step 3: Code Execution

```bash
pytest tests/ptc/integration/test_phase1_code_execution.py -v --timeout=120
```

**Expected:** Python code executes inside container. stdout/stderr captured correctly. Return values serialized properly.

**Manual verification:**
```bash
# Quick smoke test
docker exec <CONTAINER_ID> python3 -c "print('hello from sandbox')"
```

### Step 4: Tool Calling

```bash
pytest tests/ptc/integration/test_phase1_tool_calling.py -v --timeout=120
```

**Expected:** Tool calls route to container, execute, and return structured results. File read/write tools work inside sandbox.

### Step 5: Error Handling

```bash
pytest tests/ptc/integration/test_phase1_error_handling.py -v --timeout=120
```

**Expected:** Syntax errors, runtime exceptions, and timeouts are caught and reported with proper error types. No container crashes.

**Manual verification:**
```bash
# Trigger a timeout manually
docker exec <CONTAINER_ID> python3 -c "import time; time.sleep(999)"
# Should be killed by timeout mechanism
```

### Step 6: Resource Limits

```bash
pytest tests/ptc/integration/test_phase1_resource_limits.py -v --timeout=120
```

**Expected:** Memory limits enforced. CPU limits applied. Disk write limits respected. OOM kills reported gracefully.

**Manual verification:**
```bash
# Check container resource constraints
docker inspect <CONTAINER_ID> --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'

# Check live resource usage
docker stats --no-stream --filter name=ptc-
```

## Data Collection

| Metric | Value | Notes |
|--------|-------|-------|
| Container creation time | ___ s | From API call to ready state |
| pip install time (numpy) | ___ s | Single package cold install |
| First code execution time | ___ s | print("hello") round-trip |
| Tool call RTT | ___ ms | Simple file-read tool |
| Container idle memory | ___ MB | After creation, no workload |
| Container peak memory | ___ MB | During pip install |

```bash
# Collect resource snapshot
docker stats --no-stream --filter name=ptc- --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}"
```

## Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| All pytest tests pass | 0 failures | Any failure |
| Container creation time | < 5 s | > 10 s |
| Code execution round-trip | < 2 s | > 5 s |
| Container memory (idle) | < 100 MB | > 200 MB |
| Error handling coverage | All error types caught | Unhandled exceptions |
| No zombie containers | `docker ps -a --filter name=ptc-` empty after tests | Leftover containers |
