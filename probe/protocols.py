"""
probe/protocols.py
==================
Layer 3 — Protocol Benchmarks

Measures actual transfer throughput using common tools:

  Local (always run — establishes on-host copy baseline):
    python-copy   : shutil.copy2  (cross-platform)
    robocopy      : Windows local copy
    rsync         : macOS/Linux local copy

  Network (requires --target HOST with SSH key / agent auth):
    scp           : OpenSSH encrypted transfer
    rsync-ssh     : rsync over SSH
    robocopy-unc  : Windows SMB/UNC  (robocopy \\\\target\\share)

Each result is a dict appended to artifact["protocol_results"].

Schema per result
-----------------
{
    "timestamp":        "<ISO-8601 UTC>",
    "protocol":         "python-copy" | "robocopy" | "rsync" | "scp" | "rsync-ssh" | "robocopy-unc",
    "direction":        "local" | "send" | "receive",
    "target":           null | "<host>",
    "payload_size_MB":  256,
    "duration_s":       12.345,
    "throughput_MBps":  20.74,
    "throughput_Mbps":  165.9,
    "notes":            "...",
    "error":            null | "<message>",
}

Public API
----------
    probe_protocols(target=None, payload_mb=256) -> list[dict]
"""

import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .platform_utils import detect_os, run_command, tool_available

_OS              = detect_os()
_DEFAULT_PAYLOAD = 256   # MB


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def probe_protocols(
    target: Optional[str] = None,
    payload_mb: int = _DEFAULT_PAYLOAD,
) -> List[Dict[str, Any]]:
    """
    Run protocol benchmarks and return a list of result records.

    Always runs local-copy benchmarks.  Network benchmarks are added when
    *target* is given and the required tools are available.
    """
    results: List[Dict[str, Any]] = []

    # ── Generate test payload ─────────────────────────────────────────────────
    print(f"  Generating {payload_mb} MB test payload…")
    try:
        src_path = _make_payload(payload_mb)
    except Exception as exc:
        return [_error_result("payload-gen", "local", None, payload_mb,
                              f"Failed to create payload: {exc}")]

    try:
        # ── Local benchmarks (always) ─────────────────────────────────────────
        print("  [3.1] python-copy …", end=" ", flush=True)
        res = _bench_python_copy(src_path, payload_mb)
        results.append(res)
        _print_result(res)

        if _OS == "windows":
            if tool_available("robocopy"):
                print("  [3.2] robocopy (local) …", end=" ", flush=True)
                res = _bench_robocopy_local(src_path, payload_mb)
                results.append(res)
                _print_result(res)
        else:
            if tool_available("rsync"):
                print("  [3.2] rsync (local) …", end=" ", flush=True)
                res = _bench_rsync_local(src_path, payload_mb)
                results.append(res)
                _print_result(res)

        # ── Network benchmarks ────────────────────────────────────────────────
        if target:
            print(f"  Running network benchmarks → {target}")

            if tool_available("scp"):
                print("  [3.3] scp (send) …", end=" ", flush=True)
                res = _bench_scp(src_path, target, payload_mb)
                results.append(res)
                _print_result(res)

            if tool_available("rsync"):
                print("  [3.4] rsync-ssh (send) …", end=" ", flush=True)
                res = _bench_rsync_ssh(src_path, target, payload_mb)
                results.append(res)
                _print_result(res)

            if _OS == "windows" and tool_available("robocopy"):
                print("  [3.5] robocopy-UNC (send) …", end=" ", flush=True)
                res = _bench_robocopy_unc(src_path, target, payload_mb)
                results.append(res)
                _print_result(res)
        else:
            print("  No --target specified — skipping network protocol benchmarks.")

    finally:
        try:
            os.remove(src_path)
        except OSError:
            pass

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Payload generation
# ═════════════════════════════════════════════════════════════════════════════

def _make_payload(size_mb: int) -> str:
    """
    Write a temp file filled with pseudo-random bytes; return its path.
    One random MB chunk is generated and repeated to keep entropy cost low
    while still defeating compression-based shortcuts in the OS copy path.
    """
    fd, path = tempfile.mkstemp(prefix="stsc_payload_", suffix=".bin")
    chunk = os.urandom(1024 * 1024)   # 1 MB of random bytes
    try:
        with os.fdopen(fd, "wb") as f:
            for _ in range(size_mb):
                f.write(chunk)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path


# ═════════════════════════════════════════════════════════════════════════════
# Result helpers
# ═════════════════════════════════════════════════════════════════════════════

def _result(
    protocol:   str,
    direction:  str,
    target:     Optional[str],
    payload_mb: int,
    duration_s: float,
    notes:      str = "",
    **kwargs,
) -> Dict[str, Any]:
    mbps = round(payload_mb / duration_s, 2) if duration_s and duration_s > 0 else None
    return {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "protocol":        protocol,
        "direction":       direction,
        "target":          target,
        "payload_size_MB": payload_mb,
        "duration_s":      round(duration_s, 3),
        "throughput_MBps": mbps,
        "throughput_Mbps": round(mbps * 8, 1) if mbps else None,
        "notes":           notes,
        "error":           None,
        **kwargs,
    }


def _error_result(
    protocol:   str,
    direction:  str,
    target:     Optional[str],
    payload_mb: int,
    error:      str,
) -> Dict[str, Any]:
    return {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "protocol":        protocol,
        "direction":       direction,
        "target":          target,
        "payload_size_MB": payload_mb,
        "duration_s":      None,
        "throughput_MBps": None,
        "throughput_Mbps": None,
        "notes":           "",
        "error":           error,
    }


def _print_result(res: Dict[str, Any]) -> None:
    """One-line progress output after each benchmark completes."""
    if res.get("error"):
        print(f"ERROR — {res['error'][:80]}")
    else:
        mbps = res.get("throughput_MBps")
        print(f"{mbps:>8.1f} MB/s" if mbps else "—")


# ═════════════════════════════════════════════════════════════════════════════
# Local benchmarks
# ═════════════════════════════════════════════════════════════════════════════

def _bench_python_copy(src: str, payload_mb: int) -> Dict[str, Any]:
    """
    Baseline: shutil.copy2 to a temp destination on the same volume.
    Includes an fsync to flush the page-cache write so we measure real I/O,
    not just memory-copy speed.
    """
    protocol = "python-copy"
    fd, dst = tempfile.mkstemp(prefix="stsc_dst_", suffix=".bin")
    os.close(fd)
    try:
        t0 = time.perf_counter()
        shutil.copy2(src, dst)
        # Fsync: force OS to commit pages to storage
        with open(dst, "rb+") as f:
            if hasattr(os, "fsync"):
                os.fsync(f.fileno())
        duration = time.perf_counter() - t0
        return _result(
            protocol, "local", None, payload_mb, duration,
            notes="shutil.copy2 + fsync; same volume — pure OS copy baseline",
        )
    except Exception as exc:
        return _error_result(protocol, "local", None, payload_mb, str(exc))
    finally:
        try:
            os.remove(dst)
        except OSError:
            pass


def _bench_robocopy_local(src: str, payload_mb: int) -> Dict[str, Any]:
    """Windows: robocopy local directory copy."""
    protocol = "robocopy"
    dst_dir  = tempfile.mkdtemp(prefix="stsc_rc_")
    src_dir  = os.path.dirname(src)
    src_file = os.path.basename(src)
    try:
        t0 = time.perf_counter()
        out, err, rc = run_command(
            ["robocopy", src_dir, dst_dir, src_file,
             "/NP", "/NFL", "/NDL", "/NJH", "/NJS"],
            timeout=600,
        )
        duration = time.perf_counter() - t0
        # robocopy returns 1 on success (files copied), 0 if nothing to do
        if rc not in (0, 1):
            return _error_result(protocol, "local", None, payload_mb,
                                 f"exit {rc}: {(err or '').strip()[:200]}")
        speed_note = _parse_robocopy_speed(out)
        return _result(
            protocol, "local", None, payload_mb, duration,
            notes=speed_note or "robocopy local copy",
        )
    except Exception as exc:
        return _error_result(protocol, "local", None, payload_mb, str(exc))
    finally:
        shutil.rmtree(dst_dir, ignore_errors=True)


def _bench_rsync_local(src: str, payload_mb: int) -> Dict[str, Any]:
    """macOS/Linux: rsync local copy (no SSH)."""
    protocol = "rsync"
    fd, dst  = tempfile.mkstemp(prefix="stsc_rs_", suffix=".bin")
    os.close(fd)
    try:
        t0 = time.perf_counter()
        out, err, rc = run_command(
            ["rsync", "--inplace", src, dst],
            timeout=600,
        )
        duration = time.perf_counter() - t0
        if rc != 0:
            return _error_result(protocol, "local", None, payload_mb,
                                 f"exit {rc}: {(err or '').strip()[:200]}")
        return _result(
            protocol, "local", None, payload_mb, duration,
            notes="rsync local copy (no SSH, --inplace)",
        )
    except Exception as exc:
        return _error_result(protocol, "local", None, payload_mb, str(exc))
    finally:
        try:
            os.remove(dst)
        except OSError:
            pass


# ═════════════════════════════════════════════════════════════════════════════
# Network benchmarks
# ═════════════════════════════════════════════════════════════════════════════

def _bench_scp(src: str, target: str, payload_mb: int) -> Dict[str, Any]:
    """
    scp send to target:/tmp/<file>.
    Requires SSH key / ssh-agent authentication (BatchMode=yes enforced).
    """
    protocol = "scp"
    remote   = f"{target}:/tmp/{os.path.basename(src)}"
    try:
        t0 = time.perf_counter()
        out, err, rc = run_command(
            [
                "scp",
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                src, remote,
            ],
            timeout=600,
        )
        duration = time.perf_counter() - t0
        if rc != 0:
            return _error_result(protocol, "send", target, payload_mb,
                                 f"exit {rc}: {(err or '').strip()[:300]}")
        return _result(
            protocol, "send", target, payload_mb, duration,
            notes="scp via OpenSSH (AES-256-GCM or ChaCha20 depending on negotiation)",
        )
    except Exception as exc:
        return _error_result(protocol, "send", target, payload_mb, str(exc))


def _bench_rsync_ssh(src: str, target: str, payload_mb: int) -> Dict[str, Any]:
    """rsync over SSH to target:/tmp/<file>."""
    protocol = "rsync-ssh"
    remote   = f"{target}:/tmp/{os.path.basename(src)}"
    try:
        t0 = time.perf_counter()
        out, err, rc = run_command(
            [
                "rsync",
                "-e", "ssh -o StrictHostKeyChecking=no -o BatchMode=yes",
                "--inplace",
                src, remote,
            ],
            timeout=600,
        )
        duration = time.perf_counter() - t0
        if rc != 0:
            return _error_result(protocol, "send", target, payload_mb,
                                 f"exit {rc}: {(err or '').strip()[:300]}")
        return _result(
            protocol, "send", target, payload_mb, duration,
            notes="rsync over SSH — delta-transfer disabled (new file each run)",
        )
    except Exception as exc:
        return _error_result(protocol, "send", target, payload_mb, str(exc))


def _bench_robocopy_unc(src: str, target: str, payload_mb: int) -> Dict[str, Any]:
    """
    Windows: robocopy over a UNC path to \\\\target\\C$\\temp\\stsc_bench\\.
    Requires that the target share is accessible (admin share or custom share).
    """
    protocol = "robocopy-unc"
    unc_dir  = f"\\\\{target}\\C$\\temp\\stsc_bench"
    src_dir  = os.path.dirname(src)
    src_file = os.path.basename(src)
    try:
        t0 = time.perf_counter()
        out, err, rc = run_command(
            ["robocopy", src_dir, unc_dir, src_file,
             "/NP", "/NFL", "/NDL", "/NJH", "/NJS"],
            timeout=600,
        )
        duration = time.perf_counter() - t0
        if rc not in (0, 1):
            return _error_result(protocol, "send", target, payload_mb,
                                 f"exit {rc}: {(err or '').strip()[:200]}")
        speed_note = _parse_robocopy_speed(out)
        return _result(
            protocol, "send", target, payload_mb, duration,
            notes=speed_note or f"robocopy → \\\\{target}\\C$\\temp (SMB/UNC)",
        )
    except Exception as exc:
        return _error_result(protocol, "send", target, payload_mb, str(exc))


# ═════════════════════════════════════════════════════════════════════════════
# Output parsers
# ═════════════════════════════════════════════════════════════════════════════

def _parse_robocopy_speed(output: str) -> Optional[str]:
    """Extract the 'Speed : X Bytes/sec' or 'Speed : X MegaBytes/min' line."""
    for line in (output or "").splitlines():
        ll = line.lower()
        if "speed" in ll and ("byte" in ll or "mega" in ll):
            return line.strip()
    return None
