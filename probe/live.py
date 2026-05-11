"""
probe/live.py
=============
Layer 5 — Live System Telemetry During Transfer

Runs a controlled transfer (same python-copy baseline from Layer 3) while
a background sampler thread captures system counters at 1-second intervals.

Metrics sampled
---------------
  CPU      : overall % and per-core peak
  Disk I/O : read + write MB/s (via psutil disk_io_counters)
  NIC      : tx + rx MB/s (via psutil net_io_counters, per-interface)
  Memory   : used GB, available GB (page pressure indicator)

After the transfer completes, samples are correlated with throughput to
produce a telemetry summary and feed bottleneck_hints.

Public API
----------
    probe_live(payload_mb=256) -> list[dict]
"""

import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False

from .platform_utils import detect_os

_OS = detect_os()


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def probe_live(payload_mb: int = 256) -> List[Dict[str, Any]]:
    """
    Run live-telemetry benchmarks.  Each benchmark is a controlled transfer
    with a background sampler capturing system state at 1-second intervals.

    Returns a list of result dicts (one per benchmark scenario).
    """
    if not _PSUTIL_OK:
        return [{
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "scenario":     "psutil-missing",
            "error":        "psutil not installed — run: pip install psutil",
            "telemetry":    None,
        }]

    results: List[Dict[str, Any]] = []

    # Scenario 1: single-stream buffered write (matches python-copy baseline)
    print(f"  [5.1] Live telemetry — single-stream buffered write ({payload_mb} MB) …")
    results.append(_run_scenario(
        label       = "single_stream_buffered",
        payload_mb  = payload_mb,
        n_threads   = 1,
        block_kb    = 256,
        fsync_end   = True,
        notes       = "Single-stream buffered write + fsync — matches python-copy baseline",
    ))
    _print_result(results[-1])

    # Scenario 2: optimal thread count from tuning (use 4 as conservative default)
    print(f"  [5.2] Live telemetry — 4-stream parallel write ({payload_mb} MB) …")
    results.append(_run_scenario(
        label       = "four_stream_parallel",
        payload_mb  = payload_mb,
        n_threads   = 4,
        block_kb    = 256,
        fsync_end   = True,
        notes       = "4 parallel writers — matches optimal thread-count from tuning sweep",
    ))
    _print_result(results[-1])

    # Scenario 3: small-file stress (many × small — worst-case metadata load)
    small_file_mb = min(payload_mb, 32)   # cap at 32 MB to avoid too many files
    print(f"  [5.3] Live telemetry — small-file stress ({small_file_mb} MB × 1 KB files) …")
    results.append(_run_small_file_scenario(
        label       = "small_file_stress",
        payload_mb  = small_file_mb,
        file_kb     = 4,
        notes       = "Many × 4 KB files — exposes per-inode and metadata overhead",
    ))
    _print_result(results[-1])

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Sampler
# ═════════════════════════════════════════════════════════════════════════════

class _Sampler:
    """
    Background thread that snapshots psutil counters once per second.
    Call start() before the transfer, stop() after, then read .samples.
    """

    INTERVAL_S = 1.0

    def __init__(self):
        self.samples: List[Dict[str, Any]] = []
        self._stop_event = threading.Event()
        self._thread     = threading.Thread(target=self._run, daemon=True)

        # Baseline snapshots for delta calculations
        try:
            self._disk_t0 = psutil.disk_io_counters()
            self._net_t0  = psutil.net_io_counters()
        except Exception:
            self._disk_t0 = None
            self._net_t0  = None

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._thread.join(timeout=3)

    def _run(self):
        prev_disk = self._disk_t0
        prev_net  = self._net_t0
        prev_time = time.perf_counter()

        while not self._stop_event.is_set():
            time.sleep(self.INTERVAL_S)
            now = time.perf_counter()
            dt  = now - prev_time
            prev_time = now

            sample: Dict[str, Any] = {"t": round(now, 2)}

            # CPU
            try:
                sample["cpu_pct_total"]    = psutil.cpu_percent(interval=None)
                sample["cpu_pct_per_core"] = psutil.cpu_percent(interval=None, percpu=True)
            except Exception:
                sample["cpu_pct_total"] = None

            # Memory
            try:
                mem = psutil.virtual_memory()
                sample["mem_used_GB"]      = round(mem.used  / 1e9, 2)
                sample["mem_available_GB"] = round(mem.available / 1e9, 2)
            except Exception:
                sample["mem_used_GB"] = None

            # Disk I/O deltas
            try:
                cur_disk = psutil.disk_io_counters()
                if prev_disk and cur_disk and dt > 0:
                    sample["disk_read_MBps"]  = round(
                        (cur_disk.read_bytes  - prev_disk.read_bytes)  / dt / 1e6, 1)
                    sample["disk_write_MBps"] = round(
                        (cur_disk.write_bytes - prev_disk.write_bytes) / dt / 1e6, 1)
                prev_disk = cur_disk
            except Exception:
                sample["disk_read_MBps"] = None

            # NIC I/O deltas (aggregate across all interfaces)
            try:
                cur_net = psutil.net_io_counters()
                if prev_net and cur_net and dt > 0:
                    sample["nic_tx_MBps"] = round(
                        (cur_net.bytes_sent - prev_net.bytes_sent) / dt / 1e6, 1)
                    sample["nic_rx_MBps"] = round(
                        (cur_net.bytes_recv - prev_net.bytes_recv) / dt / 1e6, 1)
                prev_net = cur_net
            except Exception:
                sample["nic_tx_MBps"] = None

            self.samples.append(sample)


def _summarise_samples(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reduce raw 1-second samples into scalar summary statistics."""
    if not samples:
        return {"sample_count": 0}

    def _avg(key):
        vals = [s[key] for s in samples if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def _peak(key):
        vals = [s[key] for s in samples if s.get(key) is not None]
        return round(max(vals), 1) if vals else None

    def _min(key):
        vals = [s[key] for s in samples if s.get(key) is not None]
        return round(min(vals), 1) if vals else None

    # Per-core peak: max value seen on any core across all samples
    per_core_peak = None
    all_per_core  = [s.get("cpu_pct_per_core") for s in samples
                     if s.get("cpu_pct_per_core")]
    if all_per_core:
        n_cores = len(all_per_core[0])
        per_core_peak = [
            round(max(sample[c] for sample in all_per_core), 1)
            for c in range(n_cores)
        ]

    return {
        "sample_count":          len(samples),
        "interval_s":            _Sampler.INTERVAL_S,
        "cpu_pct_avg":           _avg("cpu_pct_total"),
        "cpu_pct_peak":          _peak("cpu_pct_total"),
        "cpu_pct_per_core_peak": per_core_peak,
        "disk_read_MBps_avg":    _avg("disk_read_MBps"),
        "disk_read_MBps_peak":   _peak("disk_read_MBps"),
        "disk_write_MBps_avg":   _avg("disk_write_MBps"),
        "disk_write_MBps_peak":  _peak("disk_write_MBps"),
        "nic_tx_MBps_avg":       _avg("nic_tx_MBps"),
        "nic_rx_MBps_avg":       _avg("nic_rx_MBps"),
        "mem_used_GB_avg":       _avg("mem_used_GB"),
        "mem_available_GB_min":  _min("mem_available_GB"),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Benchmark scenarios
# ═════════════════════════════════════════════════════════════════════════════

_WRITE_CHUNK = 256 * 1024   # 256 KB write chunk inside each worker


def _worker_write(chunk: bytes, dst: str, total_bytes: int, fsync_end: bool):
    """Write total_bytes to dst using chunk as the source pattern."""
    written = 0
    with open(dst, "wb") as f:
        while written < total_bytes:
            take = min(len(chunk), total_bytes - written)
            f.write(chunk[:take])
            written += take
        if fsync_end and hasattr(os, "fsync"):
            os.fsync(f.fileno())


def _run_scenario(
    label:      str,
    payload_mb: int,
    n_threads:  int,
    block_kb:   int,
    fsync_end:  bool,
    notes:      str,
) -> Dict[str, Any]:
    """Run a multi-threaded write scenario with live telemetry capture."""
    import threading as _threading

    chunk      = os.urandom(block_kb * 1024)
    total_bytes = payload_mb * 1024 * 1024
    seg_bytes   = total_bytes // n_threads
    dsts        = [tempfile.mktemp(prefix="stsc_live_", suffix=".bin")
                   for _ in range(n_threads)]

    sampler = _Sampler()
    sampler.start()

    try:
        t0 = time.perf_counter()
        threads = [
            _threading.Thread(
                target=_worker_write,
                args=(chunk, dst, seg_bytes, fsync_end),
                daemon=True,
            )
            for dst in dsts
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        duration = time.perf_counter() - t0
    finally:
        sampler.stop()
        for dst in dsts:
            try:
                os.remove(dst)
            except OSError:
                pass

    actual_mb = (seg_bytes * n_threads) / (1024 * 1024)
    mbps      = round(actual_mb / duration, 2) if duration > 0 else None
    telemetry = _summarise_samples(sampler.samples)

    return {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "scenario":         label,
        "n_threads":        n_threads,
        "block_kb":         block_kb,
        "payload_size_MB":  round(actual_mb, 1),
        "duration_s":       round(duration, 3),
        "throughput_MBps":  mbps,
        "telemetry":        telemetry,
        "notes":            notes,
        "error":            None,
    }


def _run_small_file_scenario(
    label:      str,
    payload_mb: int,
    file_kb:    int,
    notes:      str,
) -> Dict[str, Any]:
    """Write many small files with live telemetry capture."""
    chunk      = os.urandom(file_kb * 1024)
    file_bytes = file_kb * 1024
    n_files    = (payload_mb * 1024 * 1024) // file_bytes
    n_files    = min(n_files, 4096)   # safety cap
    actual_mb  = (n_files * file_bytes) / (1024 * 1024)

    dst_dir = tempfile.mkdtemp(prefix="stsc_live_sf_")
    sampler = _Sampler()
    sampler.start()

    try:
        t0 = time.perf_counter()
        for i in range(n_files):
            fpath = os.path.join(dst_dir, f"f{i:06d}.bin")
            with open(fpath, "wb") as f:
                f.write(chunk)
        duration = time.perf_counter() - t0
    finally:
        sampler.stop()
        shutil.rmtree(dst_dir, ignore_errors=True)

    mbps      = round(actual_mb / duration, 2) if duration > 0 else None
    telemetry = _summarise_samples(sampler.samples)

    return {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "scenario":         label,
        "n_threads":        1,
        "block_kb":         file_kb,
        "payload_size_MB":  round(actual_mb, 1),
        "duration_s":       round(duration, 3),
        "throughput_MBps":  mbps,
        "telemetry":        telemetry,
        "notes":            notes,
        "error":            None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Output helper
# ═════════════════════════════════════════════════════════════════════════════

def _print_result(res: Dict[str, Any]) -> None:
    if res.get("error"):
        print(f"    ERROR: {res['error'][:80]}")
        return
    mbps = res.get("throughput_MBps")
    tel  = res.get("telemetry") or {}
    cpu  = tel.get("cpu_pct_peak")
    dw   = tel.get("disk_write_MBps_peak")
    samples = tel.get("sample_count", 0)
    print(
        f"    {mbps:>8.1f} MB/s  |  "
        f"CPU peak {cpu}%  |  "
        f"disk write peak {dw} MB/s  |  "
        f"{samples} sample(s)"
    )
