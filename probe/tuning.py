"""
probe/tuning.py
===============
Layer 4 — Transfer Tuning Sweeps

Systematically varies one parameter at a time to find the optimal
configuration for this specific hardware+OS combination.

Sweeps run entirely locally (no --target needed) so they are always
available. Results feed directly into artifact["tuning_results"] and
drive bottleneck_hints.

Sweeps
------
  block_size    : write throughput vs I/O chunk size (4 KB → 8 MB)
  thread_count  : parallel-copy throughput vs worker count (1 → 8)
  compression   : buffered write throughput with zlib vs raw
  sync_mode     : buffered (OS-cached) vs unbuffered (fsync-per-write)
  file_profile  : many small files vs few large files (same total bytes)

Public API
----------
    probe_tuning(payload_mb=64) -> list[dict]
"""

import os
import shutil
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .platform_utils import detect_os

_OS              = detect_os()
_DEFAULT_PAYLOAD = 64    # MB per sweep point — keeps total runtime < 90 s


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def probe_tuning(payload_mb: int = _DEFAULT_PAYLOAD) -> List[Dict[str, Any]]:
    """
    Run all tuning sweeps and return a list of result records.
    Each sweep varies one parameter while holding others constant.
    """
    results: List[Dict[str, Any]] = []

    # ── Generate a reusable random source chunk ───────────────────────────────
    # 1 MB of random data, repeated to reach payload_mb without re-randomising.
    # Using random bytes ensures the OS write path cannot shortcut via
    # zero-page deduplication, but compression sweeps explicitly test
    # compressible data separately.
    chunk_1mb = os.urandom(1024 * 1024)

    print(f"  [4.1] Block size sweep ({payload_mb} MB per point) …")
    results += _sweep_block_size(chunk_1mb, payload_mb)

    print(f"  [4.2] Thread count sweep ({payload_mb} MB per point) …")
    results += _sweep_thread_count(chunk_1mb, payload_mb)

    print(f"  [4.3] Compression sweep ({payload_mb} MB per point) …")
    results += _sweep_compression(payload_mb)

    print(f"  [4.4] Sync mode sweep ({payload_mb} MB per point) …")
    results += _sweep_sync_mode(chunk_1mb, payload_mb)

    print(f"  [4.5] File profile sweep ({payload_mb} MB total) …")
    results += _sweep_file_profile(chunk_1mb, payload_mb)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Result helpers
# ═════════════════════════════════════════════════════════════════════════════

def _result(
    sweep:      str,
    variable:   str,
    value:      Any,
    payload_mb: float,
    duration_s: float,
    notes:      str = "",
) -> Dict[str, Any]:
    mbps = round(payload_mb / duration_s, 2) if duration_s > 0 else None
    return {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "sweep":           sweep,
        "variable":        variable,
        "value":           value,
        "payload_size_MB": round(payload_mb, 2),
        "duration_s":      round(duration_s, 3),
        "throughput_MBps": mbps,
        "notes":           notes,
        "error":           None,
    }


def _error_result(sweep: str, variable: str, value: Any, error: str) -> Dict[str, Any]:
    return {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "sweep":           sweep,
        "variable":        variable,
        "value":           value,
        "payload_size_MB": None,
        "duration_s":      None,
        "throughput_MBps": None,
        "notes":           "",
        "error":           error,
    }


def _tmpfile() -> str:
    fd, path = tempfile.mkstemp(prefix="stsc_tune_", suffix=".bin")
    os.close(fd)
    return path


# ═════════════════════════════════════════════════════════════════════════════
# Sweep 1 — Block size
# ═════════════════════════════════════════════════════════════════════════════

_BLOCK_SIZES_KB = [4, 16, 64, 256, 1024, 4096, 8192]


def _sweep_block_size(chunk_1mb: bytes, payload_mb: int) -> List[Dict[str, Any]]:
    """
    Write *payload_mb* MB to a temp file using different I/O chunk sizes.
    Measures how the OS I/O scheduler and filesystem respond to block size.
    Larger blocks favour sequential workloads; smaller blocks favour IOPS-heavy
    paths and show whether the filesystem has per-write overhead.
    """
    results = []
    total_bytes = payload_mb * 1024 * 1024

    for block_kb in _BLOCK_SIZES_KB:
        block_bytes = block_kb * 1024
        dst = _tmpfile()
        try:
            t0 = time.perf_counter()
            written = 0
            with open(dst, "wb") as f:
                while written < total_bytes:
                    to_write = min(block_bytes, total_bytes - written)
                    # Slice from the 1 MB chunk (cycling)
                    offset = written % len(chunk_1mb)
                    avail  = len(chunk_1mb) - offset
                    if avail >= to_write:
                        f.write(chunk_1mb[offset:offset + to_write])
                    else:
                        f.write(chunk_1mb[offset:])
                        remaining = to_write - avail
                        while remaining > 0:
                            take = min(remaining, len(chunk_1mb))
                            f.write(chunk_1mb[:take])
                            remaining -= take
                    written += to_write
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            duration = time.perf_counter() - t0
            label = f"{block_kb} KB" if block_kb < 1024 else f"{block_kb//1024} MB"
            print(f"    block={label:<7} {payload_mb / duration:>8.1f} MB/s")
            results.append(_result(
                "block_size", "block_size_KB", block_kb, payload_mb, duration,
                notes=f"sequential write, {label} blocks, fsync",
            ))
        except Exception as exc:
            results.append(_error_result("block_size", "block_size_KB", block_kb, str(exc)))
        finally:
            try:
                os.remove(dst)
            except OSError:
                pass

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Sweep 2 — Thread count
# ═════════════════════════════════════════════════════════════════════════════

_THREAD_COUNTS = [1, 2, 4, 8]
_THREAD_BLOCK  = 256 * 1024   # 256 KB per write within each worker


def _write_segment(src_chunk: bytes, dst_path: str, seg_bytes: int) -> None:
    """Write seg_bytes of data to dst_path (used by thread workers)."""
    written = 0
    with open(dst_path, "wb") as f:
        while written < seg_bytes:
            take = min(len(src_chunk), seg_bytes - written)
            f.write(src_chunk[:take])
            written += take
        if hasattr(os, "fsync"):
            os.fsync(f.fileno())


def _sweep_thread_count(chunk_1mb: bytes, payload_mb: int) -> List[Dict[str, Any]]:
    """
    Split *payload_mb* across N workers, each writing its own temp file,
    all starting simultaneously. Measures aggregate throughput vs thread count.

    Note: On a single NVMe, threads typically help up to ~2–4 because the
    controller has its own queue depth. On a spinning disk they usually hurt
    due to head thrashing. This sweep reveals that inflection point.
    """
    results = []
    total_bytes = payload_mb * 1024 * 1024

    for n_threads in _THREAD_COUNTS:
        seg_bytes = total_bytes // n_threads
        dsts = [_tmpfile() for _ in range(n_threads)]
        try:
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n_threads) as pool:
                futs = [
                    pool.submit(_write_segment, chunk_1mb, dst, seg_bytes)
                    for dst in dsts
                ]
                for f in as_completed(futs):
                    f.result()   # re-raise any worker exception
            duration = time.perf_counter() - t0
            actual_mb = (seg_bytes * n_threads) / (1024 * 1024)
            print(f"    threads={n_threads}  {actual_mb / duration:>8.1f} MB/s")
            results.append(_result(
                "thread_count", "thread_count", n_threads, actual_mb, duration,
                notes=f"{n_threads} parallel writer(s), {actual_mb:.0f} MB total",
            ))
        except Exception as exc:
            results.append(_error_result("thread_count", "thread_count", n_threads, str(exc)))
        finally:
            for d in dsts:
                try:
                    os.remove(d)
                except OSError:
                    pass

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Sweep 3 — Compression
# ═════════════════════════════════════════════════════════════════════════════

_COMPRESS_BLOCK = 1024 * 1024   # 1 MB compress chunk


def _sweep_compression(payload_mb: int) -> List[Dict[str, Any]]:
    """
    Compare write throughput for:
      - raw random data (incompressible)
      - raw compressible data (repeated pattern)
      - zlib-compressed random data written to disk
      - zlib-compressed compressible data written to disk

    The ratio of raw-compressible to zlib-compressed reveals whether the
    OS/filesystem already handles compressible data transparently (APFS
    transparent compression, ZFS, etc.) or whether explicit compression
    is worth its CPU cost.
    """
    results = []
    total_bytes = payload_mb * 1024 * 1024

    # Source data variants
    random_chunk = os.urandom(_COMPRESS_BLOCK)
    repeat_chunk = b"\xAB\xCD" * (_COMPRESS_BLOCK // 2)   # highly compressible

    variants = [
        ("raw_random",        random_chunk, False,
         "uncompressed random bytes — incompressible baseline"),
        ("raw_compressible",  repeat_chunk, False,
         "uncompressed repetitive bytes — OS/FS compression baseline"),
        ("zlib_random",       random_chunk, True,
         "zlib(random) — compression adds CPU cost, no size benefit"),
        ("zlib_compressible", repeat_chunk, True,
         "zlib(repetitive) — maximum compression benefit case"),
    ]

    for label, chunk, compress, note in variants:
        dst = _tmpfile()
        try:
            t0 = time.perf_counter()
            written_logical = 0
            with open(dst, "wb") as f:
                while written_logical < total_bytes:
                    take = min(_COMPRESS_BLOCK, total_bytes - written_logical)
                    data = chunk[:take]
                    if compress:
                        data = zlib.compress(data, level=1)   # speed > ratio
                    f.write(data)
                    written_logical += take
                if hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            duration = time.perf_counter() - t0
            # Logical MB/s (input data rate, not bytes-on-disk rate)
            mbps = payload_mb / duration
            print(f"    {label:<22} {mbps:>8.1f} MB/s")
            results.append(_result(
                "compression", "compression_mode", label, payload_mb, duration,
                notes=note,
            ))
        except Exception as exc:
            results.append(_error_result("compression", "compression_mode", label, str(exc)))
        finally:
            try:
                os.remove(dst)
            except OSError:
                pass

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Sweep 4 — Sync mode
# ═════════════════════════════════════════════════════════════════════════════

_SYNC_BLOCK = 256 * 1024   # 256 KB per write


def _sweep_sync_mode(chunk_1mb: bytes, payload_mb: int) -> List[Dict[str, Any]]:
    """
    Compare three I/O durability modes:
      buffered   : standard Python open() — OS page cache absorbs writes
      fsync_end  : buffered write + single fsync at the end (our normal mode)
      fsync_each : fsync after every write chunk — simulates synchronous I/O

    The gap between buffered and fsync_end shows cache-flush overhead.
    The gap between fsync_end and fsync_each shows per-write commit cost —
    important for database-style workloads or write-heavy transfer tools
    that call fsync frequently.
    """
    results = []
    total_bytes = payload_mb * 1024 * 1024

    modes = [
        ("buffered",   False, False, "OS page cache only — no fsync (fast but not durable)"),
        ("fsync_end",  True,  False, "buffered write + one fsync at close (normal mode)"),
        ("fsync_each", False, True,  "fsync after every 256 KB chunk (synchronous I/O simulation)"),
    ]

    for label, fsync_at_end, fsync_each_write, note in modes:
        dst = _tmpfile()
        try:
            t0 = time.perf_counter()
            written = 0
            with open(dst, "wb") as f:
                while written < total_bytes:
                    take = min(_SYNC_BLOCK, total_bytes - written)
                    offset = written % len(chunk_1mb)
                    avail  = len(chunk_1mb) - offset
                    if avail >= take:
                        f.write(chunk_1mb[offset:offset + take])
                    else:
                        f.write(chunk_1mb[offset:])
                        rem = take - avail
                        while rem > 0:
                            t2 = min(rem, len(chunk_1mb))
                            f.write(chunk_1mb[:t2])
                            rem -= t2
                    written += take
                    if fsync_each_write and hasattr(os, "fsync"):
                        os.fsync(f.fileno())
                if fsync_at_end and hasattr(os, "fsync"):
                    os.fsync(f.fileno())
            duration = time.perf_counter() - t0
            print(f"    {label:<14} {payload_mb / duration:>8.1f} MB/s")
            results.append(_result(
                "sync_mode", "sync_mode", label, payload_mb, duration,
                notes=note,
            ))
        except Exception as exc:
            results.append(_error_result("sync_mode", "sync_mode", label, str(exc)))
        finally:
            try:
                os.remove(dst)
            except OSError:
                pass

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Sweep 5 — File profile (many small vs few large)
# ═════════════════════════════════════════════════════════════════════════════

_FILE_PROFILES = [
    (1024,  1),      # 1024 × 1 KB   = 1 MB total (scaled to payload_mb)
    (256,   4),      # 256  × 4 KB
    (64,    16),     # 64   × 16 KB
    (16,    64),     # 16   × 64 KB
    (4,     256),    # 4    × 256 KB
    (1,     1024),   # 1    × 1 MB   (baseline per-file)
]


def _sweep_file_profile(chunk_1mb: bytes, payload_mb: int) -> List[Dict[str, Any]]:
    """
    Write the same total payload distributed across different file count / size
    combinations. Shows the per-file overhead cost — critical for understanding
    why many small files transfer far slower than equivalent bytes in large files.

    Many transfer bottlenecks appear only with small files:
      - SSH/SCP: per-file handshake cost
      - rsync: checksum overhead per file
      - SMB: per-request round-trip
      - Filesystem: metadata write per inode
    """
    results = []

    # Scale the profile counts to reach payload_mb
    # _FILE_PROFILES are defined for 1 MB total; multiply by payload_mb
    for n_files_base, file_kb_base in _FILE_PROFILES:
        n_files  = n_files_base * payload_mb
        file_kb  = file_kb_base
        file_bytes = file_kb * 1024
        actual_mb  = (n_files * file_bytes) / (1024 * 1024)

        # Cap at 4096 files to avoid excessive tempfile overhead
        if n_files > 4096:
            n_files  = 4096
            actual_mb = (n_files * file_bytes) / (1024 * 1024)

        dst_dir = tempfile.mkdtemp(prefix="stsc_fp_")
        try:
            t0 = time.perf_counter()
            for i in range(n_files):
                fpath = os.path.join(dst_dir, f"f{i:06d}.bin")
                with open(fpath, "wb") as f:
                    written = 0
                    while written < file_bytes:
                        take = min(len(chunk_1mb), file_bytes - written)
                        f.write(chunk_1mb[:take])
                        written += take
            duration = time.perf_counter() - t0
            mbps = actual_mb / duration
            label = f"{n_files}×{file_kb}KB"
            print(f"    {label:<16} {mbps:>8.1f} MB/s  ({actual_mb:.0f} MB)")
            results.append(_result(
                "file_profile", "file_count×size", f"{n_files}×{file_kb}KB",
                actual_mb, duration,
                notes=f"{n_files} files × {file_kb} KB = {actual_mb:.0f} MB",
            ))
        except Exception as exc:
            results.append(_error_result(
                "file_profile", "file_count×size", f"{n_files}×{file_kb}KB", str(exc)
            ))
        finally:
            shutil.rmtree(dst_dir, ignore_errors=True)

    return results
