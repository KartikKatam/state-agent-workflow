from __future__ import annotations

import csv
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class ResourceMonitor:
    """Daemon thread that samples system resources every 5s and writes to CSV."""

    CSV_COLUMNS = [
        "timestamp",
        "ram_total_mb",
        "ram_used_mb",
        "ram_pct",
        "cpu_pct_avg",
        "cpu_pct_max",
        "gpu_mem_used_mb",
        "gpu_mem_total_mb",
        "gpu_util_pct",
        "docker_container_count",
        "docker_mem_total_mb",
        "ptc_container_count",
        "ptc_repl_count",
    ]

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._csv_path: Path | None = None

    def start(self, csv_path: Path) -> None:
        """Start the daemon sampling thread, writing rows to *csv_path*."""
        self._csv_path = Path(csv_path)
        self._stop_event.clear()

        # Write CSV header
        with self._csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the sampling thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def get_snapshot(self) -> dict[str, Any]:
        """Return the current resource readings as a dict."""
        return self._sample()

    # ── internals ──────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_event.is_set():
            snapshot = self._sample()
            self._write_row(snapshot)
            self._stop_event.wait(timeout=5)

    def _write_row(self, row: dict[str, Any]) -> None:
        if self._csv_path is None:
            return
        with self._lock:
            with self._csv_path.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=self.CSV_COLUMNS)
                writer.writerow(row)

    @staticmethod
    def _sample() -> dict[str, Any]:
        snap: dict[str, Any] = {"timestamp": time.time()}
        _sample_cpu_ram(snap)
        _sample_gpu(snap)
        _sample_docker(snap)
        return snap


# ── sampling helpers (module-level for testability) ───────────


def _sample_cpu_ram(snap: dict[str, Any]) -> None:
    try:
        import psutil

        vm = psutil.virtual_memory()
        snap["ram_total_mb"] = round(vm.total / 1_048_576, 1)
        snap["ram_used_mb"] = round(vm.used / 1_048_576, 1)
        snap["ram_pct"] = vm.percent

        cpus = psutil.cpu_percent(interval=0.1, percpu=True)
        snap["cpu_pct_avg"] = round(sum(cpus) / len(cpus), 1) if cpus else 0.0
        snap["cpu_pct_max"] = round(max(cpus), 1) if cpus else 0.0
    except Exception:
        snap.setdefault("ram_total_mb", 0)
        snap.setdefault("ram_used_mb", 0)
        snap.setdefault("ram_pct", 0.0)
        snap.setdefault("cpu_pct_avg", 0.0)
        snap.setdefault("cpu_pct_max", 0.0)


def _sample_gpu(snap: dict[str, Any]) -> None:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            parts = out.stdout.strip().splitlines()[0].split(",")
            snap["gpu_mem_used_mb"] = float(parts[0].strip())
            snap["gpu_mem_total_mb"] = float(parts[1].strip())
            snap["gpu_util_pct"] = float(parts[2].strip())
            return
    except Exception:
        pass
    snap["gpu_mem_used_mb"] = 0
    snap["gpu_mem_total_mb"] = 0
    snap["gpu_util_pct"] = 0.0


def _sample_docker(snap: dict[str, Any]) -> None:
    try:
        # Container count + memory via docker stats
        stats_out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        container_count = 0
        total_mem_mb = 0.0
        if stats_out.returncode == 0:
            for line in stats_out.stdout.strip().splitlines():
                if not line.strip():
                    continue
                container_count += 1
                # MemUsage looks like "123.4MiB / 16GiB"
                parts = line.split("\t")
                if len(parts) >= 2:
                    total_mem_mb += _parse_mem(parts[1].split("/")[0].strip())

        snap["docker_container_count"] = container_count
        snap["docker_mem_total_mb"] = round(total_mem_mb, 1)

        # PTC container count
        ptc_out = subprocess.run(
            ["docker", "ps", "--filter", "name=ptc-", "-q"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        ptc_count = 0
        if ptc_out.returncode == 0:
            ptc_count = len([l for l in ptc_out.stdout.strip().splitlines() if l.strip()])
        snap["ptc_container_count"] = ptc_count

        # PTC REPL count (containers with 'repl' in name)
        repl_out = subprocess.run(
            ["docker", "ps", "--filter", "name=ptc-", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        repl_count = 0
        if repl_out.returncode == 0:
            repl_count = sum(
                1 for l in repl_out.stdout.strip().splitlines() if l.strip()
            )
        snap["ptc_repl_count"] = repl_count
    except Exception:
        snap.setdefault("docker_container_count", 0)
        snap.setdefault("docker_mem_total_mb", 0.0)
        snap.setdefault("ptc_container_count", 0)
        snap.setdefault("ptc_repl_count", 0)


def _parse_mem(text: str) -> float:
    """Parse Docker memory string like '123.4MiB' or '1.2GiB' into MB."""
    text = text.strip()
    try:
        if "GiB" in text:
            return float(text.replace("GiB", "").strip()) * 1024
        if "MiB" in text:
            return float(text.replace("MiB", "").strip())
        if "KiB" in text:
            return float(text.replace("KiB", "").strip()) / 1024
        if "B" in text:
            return float(text.replace("B", "").strip()) / 1_048_576
    except ValueError:
        pass
    return 0.0
