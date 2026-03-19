# Resource Monitoring Runbook

## Prerequisites

- [ ] Docker daemon running
- [ ] PTC sandbox image built (`docker image inspect ptc-sandbox:latest`)
- [ ] Python 3.11+ with `psutil` and `pandas` installed on host
- [ ] `matplotlib` installed on host for plotting (optional but recommended)
- [ ] Terminal multiplexer available (tmux or screen) for parallel monitoring
- [ ] Sufficient disk space for CSV logs (estimate: < 50 MB)

## Step 1: Start Resource Monitor

### Option A: Docker Stats Logger (simple)

```bash
# Start continuous logging to CSV in a background terminal
mkdir -p tests/ptc/integration/results

# Log every 2 seconds
while true; do
  docker stats --no-stream --filter name=ptc- --format \
    "$(date +%s),{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.NetIO}},{{.BlockIO}},{{.PIDs}}" \
    >> tests/ptc/integration/results/resource_log.csv
  sleep 2
done
```

Add CSV header first:
```bash
echo "timestamp,container,cpu_pct,mem_usage,mem_pct,net_io,block_io,pids" \
  > tests/ptc/integration/results/resource_log.csv
```

### Option B: psutil Host Monitor (detailed)

```python
#!/usr/bin/env python3
"""Host-level resource monitor. Run in a separate terminal."""
import psutil
import csv
import time
import subprocess
import json
from pathlib import Path

output = Path("tests/ptc/integration/results/host_resources.csv")
output.parent.mkdir(parents=True, exist_ok=True)

with open(output, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "timestamp", "host_cpu_pct", "host_mem_used_mb", "host_mem_available_mb",
        "ptc_container_count", "ptc_total_mem_mb", "ptc_total_pids"
    ])

    while True:
        ts = time.time()
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()

        # Count PTC containers and their resources
        try:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--filter", "name=ptc-",
                 "--format", "{{.MemUsage}}|||{{.PIDs}}"],
                capture_output=True, text=True, timeout=5
            )
            lines = [l for l in result.stdout.strip().split("\n") if l]
            container_count = len(lines)
            total_mem_mb = 0
            total_pids = 0
            for line in lines:
                parts = line.split("|||")
                mem_str = parts[0].split("/")[0].strip()
                if "GiB" in mem_str:
                    total_mem_mb += float(mem_str.replace("GiB", "")) * 1024
                elif "MiB" in mem_str:
                    total_mem_mb += float(mem_str.replace("MiB", ""))
                total_pids += int(parts[1].strip()) if len(parts) > 1 else 0
        except Exception:
            container_count = 0
            total_mem_mb = 0
            total_pids = 0

        writer.writerow([
            f"{ts:.1f}", f"{cpu:.1f}",
            f"{mem.used / 1024 / 1024:.0f}",
            f"{mem.available / 1024 / 1024:.0f}",
            container_count, f"{total_mem_mb:.0f}", total_pids
        ])
        f.flush()
        time.sleep(2)
```

Save as `scripts/resource_monitor.py` and run:
```bash
python3 scripts/resource_monitor.py &
MONITOR_PID=$!
echo "Monitor started with PID $MONITOR_PID"
```

## Step 2: Run Phases and Collect CSV Snapshots

Run each phase while the resource monitor is logging. Take a snapshot between phases.

### Phase 1 Snapshot

```bash
echo "=== PHASE 1 START ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

pytest tests/ptc/integration/test_phase1_container_lifecycle.py -v --timeout=120
pytest tests/ptc/integration/test_phase1_code_execution.py -v --timeout=120
pytest tests/ptc/integration/test_phase1_tool_calling.py -v --timeout=120
pytest tests/ptc/integration/test_phase1_error_handling.py -v --timeout=120
pytest tests/ptc/integration/test_phase1_resource_limits.py -v --timeout=120

echo "=== PHASE 1 END ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

# Snapshot
docker stats --no-stream --filter name=ptc- > tests/ptc/integration/results/snapshot_phase1.txt
free -m >> tests/ptc/integration/results/snapshot_phase1.txt
```

### Phase 2 Snapshot

```bash
echo "=== PHASE 2 START ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

pytest tests/ptc/integration/test_phase2_multi_agent_isolation.py -v --timeout=300
pytest tests/ptc/integration/test_phase2_concurrent_execution.py -v --timeout=300
pytest tests/ptc/integration/test_phase2_resource_contention.py -v --timeout=300
pytest tests/ptc/integration/test_phase2_role_diversity.py -v --timeout=300

echo "=== PHASE 2 END ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

docker stats --no-stream --filter name=ptc- > tests/ptc/integration/results/snapshot_phase2.txt
free -m >> tests/ptc/integration/results/snapshot_phase2.txt
```

### Phase 3 Snapshot

```bash
echo "=== PHASE 3 START ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

pytest tests/ptc/integration/test_phase3_handoff_lifecycle.py -v --timeout=120
pytest tests/ptc/integration/test_phase3_namespace_reset.py -v --timeout=120
pytest tests/ptc/integration/test_phase3_container_persistence.py -v --timeout=120
pytest tests/ptc/integration/test_phase3_idle_cleanup.py -v --timeout=300

echo "=== PHASE 3 END ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

docker stats --no-stream --filter name=ptc- > tests/ptc/integration/results/snapshot_phase3.txt
free -m >> tests/ptc/integration/results/snapshot_phase3.txt
```

### Phase 4 Snapshot

```bash
echo "=== PHASE 4 START ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

pytest tests/ptc/integration/test_phase4_sub_agent_routing.py -v --timeout=120
pytest tests/ptc/integration/test_phase4_repl_isolation.py -v --timeout=120
pytest tests/ptc/integration/test_phase4_repl_limit.py -v --timeout=120
pytest tests/ptc/integration/test_phase4_concurrent_sub_agents.py -v --timeout=120

echo "=== PHASE 4 END ===" >> tests/ptc/integration/results/phase_markers.log
date +%s >> tests/ptc/integration/results/phase_markers.log

docker stats --no-stream --filter name=ptc- > tests/ptc/integration/results/snapshot_phase4.txt
free -m >> tests/ptc/integration/results/snapshot_phase4.txt
```

### Stop Monitor

```bash
kill $MONITOR_PID 2>/dev/null
echo "Monitor stopped"
```

## Step 3: Final Analysis

### Load and Analyze CSV Data

```python
#!/usr/bin/env python3
"""Analyze resource monitoring data and generate plots."""
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

results_dir = Path("tests/ptc/integration/results")

# --- Load Docker stats CSV ---
df = pd.read_csv(results_dir / "resource_log.csv")
df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
df["elapsed_min"] = (df["timestamp"] - df["timestamp"].min()).dt.total_seconds() / 60

# Parse memory column: "123.4MiB / 512MiB" -> 123.4
def parse_mem_mb(mem_str):
    used = mem_str.split("/")[0].strip()
    if "GiB" in used:
        return float(used.replace("GiB", "")) * 1024
    elif "MiB" in used:
        return float(used.replace("MiB", ""))
    elif "KiB" in used:
        return float(used.replace("KiB", "")) / 1024
    return 0.0

df["mem_mb"] = df["mem_usage"].apply(parse_mem_mb)
df["cpu_num"] = df["cpu_pct"].str.replace("%", "").astype(float)

# --- Plot 1: RAM Usage Over Time ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

for container in df["container"].unique():
    cdf = df[df["container"] == container]
    ax1.plot(cdf["elapsed_min"], cdf["mem_mb"], label=container, linewidth=1)

ax1.set_ylabel("Memory (MB)")
ax1.set_title("PTC Container Memory Usage Over Time")
ax1.legend(fontsize=8)
ax1.grid(True, alpha=0.3)

# --- Plot 2: CPU Usage Over Time ---
for container in df["container"].unique():
    cdf = df[df["container"] == container]
    ax2.plot(cdf["elapsed_min"], cdf["cpu_num"], label=container, linewidth=1)

ax2.set_ylabel("CPU (%)")
ax2.set_xlabel("Elapsed Time (minutes)")
ax2.set_title("PTC Container CPU Usage Over Time")
ax2.legend(fontsize=8)
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(results_dir / "resource_usage_plot.png", dpi=150)
print(f"Plot saved to {results_dir / 'resource_usage_plot.png'}")

# --- Summary Statistics ---
print("\n=== Resource Usage Summary ===")
print(f"Total data points: {len(df)}")
print(f"Duration: {df['elapsed_min'].max():.1f} minutes")
print(f"Peak memory (single container): {df['mem_mb'].max():.0f} MB")
print(f"Average memory (per container): {df['mem_mb'].mean():.0f} MB")
print(f"Peak CPU (single container): {df['cpu_num'].max():.1f}%")
print(f"Max concurrent containers: {df.groupby('timestamp')['container'].count().max()}")

# --- Identify Spikes ---
mem_threshold = df["mem_mb"].quantile(0.95)
spikes = df[df["mem_mb"] > mem_threshold]
if not spikes.empty:
    print(f"\n=== Memory Spikes (> {mem_threshold:.0f} MB) ===")
    for _, row in spikes.iterrows():
        print(f"  {row['timestamp']} | {row['container']} | {row['mem_mb']:.0f} MB | CPU: {row['cpu_num']:.1f}%")
else:
    print("\nNo significant memory spikes detected.")
```

Save as `scripts/analyze_resources.py` and run:
```bash
python3 scripts/analyze_resources.py
```

### Quick CLI Analysis (no matplotlib)

```bash
# Peak memory per container
awk -F',' 'NR>1 {print $2, $4}' tests/ptc/integration/results/resource_log.csv \
  | sort -k2 -rn | head -10

# Container count over time
awk -F',' 'NR>1 {counts[$1]++} END {for (t in counts) print t, counts[t]}' \
  tests/ptc/integration/results/resource_log.csv | sort -n

# Phase duration from markers
cat tests/ptc/integration/results/phase_markers.log
```

## Data Collection Summary

| Metric | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|--------|---------|---------|---------|---------|
| Peak container memory | ___ MB | ___ MB | ___ MB | ___ MB |
| Peak host CPU | ___ % | ___ % | ___ % | ___ % |
| Max concurrent containers | ___ | ___ | ___ | ___ |
| Duration | ___ min | ___ min | ___ min | ___ min |
| Memory spikes count | ___ | ___ | ___ | ___ |

## Pass/Fail Criteria

| Criterion | Pass | Fail |
|-----------|------|------|
| No OOM kills across all phases | Zero OOM events | Any OOM kill |
| Host memory stays stable | Available > 2 GB throughout | Available < 500 MB |
| No memory leaks | Container memory stable over time | Monotonic memory growth |
| Resource log complete | CSV covers entire test run | Gaps > 30 s in log |
| All containers cleaned up | Zero PTC containers at end | Leftover containers |
