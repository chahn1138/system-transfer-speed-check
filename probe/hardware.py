"""
probe/hardware.py
=================
Layer 1 — Local Hardware Baseline

Probes disk, CPU, RAM, and NIC on Windows, Linux, and macOS.
Each OS gets its best native tooling; a pure-Python fallback ensures
something always runs even when native tools are absent.

Public API
----------
    probe_hardware() -> dict
"""

import json
import os
import re
import tempfile
import time
from typing import Any, Dict, Optional

from .platform_utils import detect_os, run_command, ps, tool_available

_OS = detect_os()

# ── Disk benchmark parameters ─────────────────────────────────────────────────
_DISK_TEST_MB  = 256          # size of the sequential R/W test file (MiB)
_BLOCK_SIZE    = 1024 * 1024  # 1 MiB per read/write call


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def probe_hardware() -> Dict[str, Any]:
    """Run all Layer 1 hardware probes and return a results dict."""
    results: Dict[str, Any] = {
        "disk": _probe_disk(),
        "cpu":  _probe_cpu(),
        "ram":  _probe_ram(),
        "nic":  _probe_nic(),
    }
    if _OS == "windows":
        results["power_plan"] = _probe_power_plan()
    return results


# ═════════════════════════════════════════════════════════════════════════════
# Layer 1a — Disk
# ═════════════════════════════════════════════════════════════════════════════

def _probe_disk() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "sequential_read_MBps":  None,
        "sequential_write_MBps": None,
        "device_type":           None,  # "SSD" | "HDD" | "NVMe" | "Unknown"
        "interface":             None,  # "NVMe" | "SATA" | "USB" | "Unknown"
        "test_file_size_MB":     _DISK_TEST_MB,
        "test_method":           "python_sequential",
    }

    # Cross-platform Python benchmark (always runs)
    try:
        r, w = _disk_sequential_python()
        result["sequential_read_MBps"]  = r
        result["sequential_write_MBps"] = w
    except Exception as e:
        result["error"] = str(e)

    # OS-native device-type info (best-effort, non-fatal)
    try:
        if _OS == "windows":
            result.update(_disk_info_windows())
        elif _OS == "linux":
            result.update(_disk_info_linux())
        elif _OS == "macos":
            result.update(_disk_info_macos())
    except Exception:
        pass

    return result


def _disk_sequential_python() -> tuple:
    """
    Write then read a temporary file of _DISK_TEST_MB size.
    Returns (read_MBps, write_MBps) as floats rounded to 2 dp.
    Uses os.fsync() to flush OS write buffers before timing the read.
    """
    size_bytes = _DISK_TEST_MB * 1024 * 1024
    block = b"\x00" * _BLOCK_SIZE

    fd, tmp_path = tempfile.mkstemp(prefix="stsc_disk_")
    os.close(fd)

    try:
        # ── Sequential Write ──────────────────────────────────────────────
        t0 = time.perf_counter()
        with open(tmp_path, "wb") as f:
            written = 0
            while written < size_bytes:
                f.write(block)
                written += _BLOCK_SIZE
            f.flush()
            os.fsync(f.fileno())
        write_mbps = round(_DISK_TEST_MB / (time.perf_counter() - t0), 2)

        # ── Sequential Read ───────────────────────────────────────────────
        t0 = time.perf_counter()
        with open(tmp_path, "rb") as f:
            while f.read(_BLOCK_SIZE):
                pass
        read_mbps = round(_DISK_TEST_MB / (time.perf_counter() - t0), 2)

        return read_mbps, write_mbps
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _disk_info_windows() -> Dict[str, Any]:
    out, _, rc = ps(
        "Get-PhysicalDisk | "
        "Select-Object FriendlyName, MediaType, BusType | "
        "ConvertTo-Json"
    )
    if rc != 0 or not out:
        return {}
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        disk = data[0]
        return {
            "device_type": _map_media_type(disk.get("MediaType", "")),
            "interface":   _map_bus_type(disk.get("BusType", "")),
        }
    except (json.JSONDecodeError, KeyError, IndexError):
        return {}


def _disk_info_linux() -> Dict[str, Any]:
    out, _, rc = run_command(["lsblk", "-d", "-o", "NAME,ROTA,TYPE,TRAN", "--json"])
    if rc != 0 or not out:
        return {}
    try:
        data = json.loads(out)
        for dev in data.get("blockdevices", []):
            if dev.get("type") == "disk":
                rota  = str(dev.get("rota", "1"))
                tran  = (dev.get("tran") or "").upper()
                dtype = "HDD" if rota == "1" else ("NVMe" if tran == "NVME" else "SSD")
                return {"device_type": dtype, "interface": tran or "Unknown"}
    except (json.JSONDecodeError, KeyError):
        pass
    return {}


def _disk_info_macos() -> Dict[str, Any]:
    out, _, rc = run_command(["diskutil", "info", "/"])
    if rc != 0 or not out:
        return {}
    dtype, iface = None, None
    for line in out.splitlines():
        lower = line.lower()
        if "solid state" in lower:
            dtype = "SSD"
        elif "rotational" in lower:
            dtype = "HDD"
        if "nvme" in lower:
            iface = "NVMe"
        elif "sata" in lower:
            iface = "SATA"
    return {k: v for k, v in {"device_type": dtype, "interface": iface}.items() if v}


def _map_media_type(mt: Any) -> str:
    """Map WMI MediaType integer to human string."""
    mapping = {3: "HDD", 4: "SSD", 5: "SCM"}
    try:
        return mapping.get(int(mt), "Unknown")
    except (ValueError, TypeError):
        return str(mt) if mt else "Unknown"


def _map_bus_type(bt: Any) -> str:
    """Map WMI BusType integer to human string."""
    mapping = {7: "USB", 11: "SATA", 14: "RAID", 17: "NVMe", 18: "SATA"}
    try:
        return mapping.get(int(bt), "Unknown")
    except (ValueError, TypeError):
        return str(bt) if bt else "Unknown"


# ═════════════════════════════════════════════════════════════════════════════
# Layer 1b — CPU
# ═════════════════════════════════════════════════════════════════════════════

def _probe_cpu() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "model":          None,
        "cores_physical": None,
        "cores_logical":  None,
        "aes_ni":         None,
    }
    try:
        if _OS == "windows":
            result.update(_cpu_windows())
        elif _OS == "linux":
            result.update(_cpu_linux())
        elif _OS == "macos":
            result.update(_cpu_macos())
    except Exception as e:
        result["error"] = str(e)
    return result


def _cpu_windows() -> Dict[str, Any]:
    out, _, rc = ps(
        "Get-CimInstance Win32_Processor | "
        "Select-Object Name, NumberOfCores, NumberOfLogicalProcessors | "
        "ConvertTo-Json"
    )
    result: Dict[str, Any] = {}
    if rc == 0 and out:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                data = data[0]
            result["model"]          = (data.get("Name") or "").strip()
            result["cores_physical"] = data.get("NumberOfCores")
            result["cores_logical"]  = data.get("NumberOfLogicalProcessors")
        except (json.JSONDecodeError, KeyError):
            pass

    # AES-NI: .NET Intrinsics (PowerShell 7 / .NET 5+)
    aes_out, _, aes_rc = ps("[System.Runtime.Intrinsics.X86.Aes]::IsSupported")
    if aes_rc == 0 and aes_out.lower() in ("true", "false"):
        result["aes_ni"] = aes_out.lower() == "true"
    else:
        # Fallback: heuristic from CPU model string
        result["aes_ni"] = _aes_heuristic(result.get("model", ""))

    return result


def _cpu_linux() -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read()
        logical_count = 0
        for line in cpuinfo.splitlines():
            if line.startswith("model name") and not result.get("model"):
                result["model"] = line.split(":", 1)[1].strip()
            if line.startswith("cpu cores") and not result.get("cores_physical"):
                try:
                    result["cores_physical"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            if line.startswith("processor"):
                try:
                    logical_count = int(line.split(":", 1)[1].strip()) + 1
                except ValueError:
                    pass
            if line.startswith("flags") and result.get("aes_ni") is None:
                flags = f" {line.split(':', 1)[1]} "
                result["aes_ni"] = " aes " in flags
        if logical_count:
            result["cores_logical"] = logical_count
    except OSError:
        pass
    return result


def _cpu_macos() -> Dict[str, Any]:
    result: Dict[str, Any] = {}

    model_out, _, _ = run_command(["sysctl", "-n", "machdep.cpu.brand_string"])
    phys_out,  _, _ = run_command(["sysctl", "-n", "hw.physicalcpu"])
    logi_out,  _, _ = run_command(["sysctl", "-n", "hw.logicalcpu"])
    aes_out,   _, _ = run_command(["sysctl", "-n", "hw.optional.aes"])

    if model_out:
        result["model"] = model_out
    if phys_out:
        try:
            result["cores_physical"] = int(phys_out)
        except ValueError:
            pass
    if logi_out:
        try:
            result["cores_logical"] = int(logi_out)
        except ValueError:
            pass
    if aes_out:
        try:
            result["aes_ni"] = int(aes_out) == 1
        except ValueError:
            pass

    # Apple Silicon: hw.optional.aes may be absent; fall back to heuristic
    if result.get("aes_ni") is None:
        result["aes_ni"] = _aes_heuristic(result.get("model", ""))

    return result


def _aes_heuristic(model: str) -> Optional[bool]:
    """
    Heuristic: Intel Sandy Bridge (2011+), AMD Bulldozer (2011+), and all
    Apple Silicon chips support AES-NI / hardware AES.
    Returns None if the model string gives no useful signal.
    """
    lower = (model or "").lower()
    if any(k in lower for k in ("apple m", "apple a", "intel", "amd ryzen",
                                 "amd epyc", "amd threadripper", "xeon")):
        return True
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Layer 1c — RAM
# ═════════════════════════════════════════════════════════════════════════════

def _probe_ram() -> Dict[str, Any]:
    result: Dict[str, Any] = {"total_GB": None}
    try:
        if _OS == "windows":
            out, _, rc = ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")
            if rc == 0 and out:
                result["total_GB"] = round(int(out) / (1024 ** 3), 2)

        elif _OS == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        result["total_GB"] = round(kb / (1024 ** 2), 2)
                        break

        elif _OS == "macos":
            out, _, rc = run_command(["sysctl", "-n", "hw.memsize"])
            if rc == 0 and out:
                result["total_GB"] = round(int(out) / (1024 ** 3), 2)

    except Exception as e:
        result["error"] = str(e)

    return result


# ═════════════════════════════════════════════════════════════════════════════
# Layer 1d — NIC
# ═════════════════════════════════════════════════════════════════════════════

def _probe_nic() -> Dict[str, Any]:
    """Return a dict with an 'interfaces' list of active NIC details."""
    try:
        if _OS == "windows":
            return _nic_windows()
        elif _OS == "linux":
            return _nic_linux()
        elif _OS == "macos":
            return _nic_macos()
    except Exception as e:
        return {"interfaces": [], "error": str(e)}
    return {"interfaces": []}


def _nic_windows() -> Dict[str, Any]:
    out, _, rc = ps(
        "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | "
        "Select-Object Name, LinkSpeed, FullDuplex, DriverDescription | "
        "ConvertTo-Json"
    )
    if rc != 0 or not out:
        return {"interfaces": []}
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        interfaces = []
        for iface in data:
            speed_str = iface.get("LinkSpeed") or ""
            interfaces.append({
                "name":                  iface.get("Name"),
                "negotiated_speed_str":  speed_str,
                "negotiated_speed_Mbps": _parse_link_speed(speed_str),
                "full_duplex":           iface.get("FullDuplex"),
                "driver":                iface.get("DriverDescription"),
            })
        return {"interfaces": interfaces}
    except (json.JSONDecodeError, KeyError):
        return {"interfaces": []}


def _nic_linux() -> Dict[str, Any]:
    out, _, rc = run_command(["ip", "-j", "link", "show"])
    if rc != 0 or not out:
        return {"interfaces": []}
    try:
        links = json.loads(out)
    except json.JSONDecodeError:
        return {"interfaces": []}

    interfaces = []
    for link in links:
        name = link.get("ifname", "")
        if name == "lo" or link.get("operstate", "").lower() != "up":
            continue
        entry: Dict[str, Any] = {
            "name":                  name,
            "negotiated_speed_Mbps": None,
            "full_duplex":           None,
        }
        if tool_available("ethtool"):
            eth_out, _, _ = run_command(["ethtool", name], timeout=5)
            for line in eth_out.splitlines():
                s = line.strip()
                if s.startswith("Speed:"):
                    entry["negotiated_speed_Mbps"] = _parse_link_speed(
                        s.split(":", 1)[1].strip()
                    )
                if s.startswith("Duplex:"):
                    entry["full_duplex"] = "full" in s.lower()
        interfaces.append(entry)

    return {"interfaces": interfaces}


def _nic_macos() -> Dict[str, Any]:
    """
    Use networksetup -listallhardwareports to enumerate ports, then
    parse each device's ifconfig block for the media line.
    Only includes interfaces that are UP.
    """
    list_out, _, rc = run_command(["networksetup", "-listallhardwareports"])
    if rc != 0 or not list_out:
        return {"interfaces": []}

    # Build port-name → device mapping
    port_map = {}
    current_port = None
    for line in list_out.splitlines():
        if line.startswith("Hardware Port:"):
            current_port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:") and current_port:
            port_map[line.split(":", 1)[1].strip()] = current_port
            current_port = None

    # Parse ifconfig for all UP interfaces
    ifc_out, _, _ = run_command(["ifconfig", "-u"])  # -u = UP only
    if not ifc_out:
        ifc_out, _, _ = run_command(["ifconfig"])

    # Split into per-device blocks
    import re as _re
    VIRTUAL_PREFIXES = ("utun", "awdl", "llw", "anpi", "vmenet", "lo", "ap")
    VIRTUAL_EXACT    = {"ap1"}

    blocks = _re.split(r"(?m)^(?=\w)", ifc_out)
    interfaces = []
    for block in blocks:
        dev_m = _re.match(r"(\w[\w.]+):", block)
        if not dev_m:
            continue
        dev = dev_m.group(1)
        if "LOOPBACK" in block:
            continue
        if dev in VIRTUAL_EXACT or any(dev.startswith(p) for p in VIRTUAL_PREFIXES):
            continue
        if "status: inactive" in block or "media: none" in block:
            continue

        speed_mbps  = None
        full_duplex = None
        media_m = _re.search(r"media:\s+\S+\s*\(([^)]+)\)", block)
        if media_m:
            media_str   = media_m.group(1)
            speed_mbps  = _parse_link_speed(media_str)
            full_duplex = "full-duplex" in media_str.lower()

        interfaces.append({
            "name":                  port_map.get(dev, dev),
            "device":                dev,
            "negotiated_speed_Mbps": speed_mbps,
            "full_duplex":           full_duplex,
        })

    return {"interfaces": interfaces}


def _parse_link_speed(s: str) -> Optional[int]:
    """
    Extract a Mbps integer from various speed strings:
      '1 Gbps', '10 Gbps', '100 Mbps', '1000baseT', '10GBase-T full-duplex'
    """
    if not s:
        return None
    # "N Gbps" or "N Mbps"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(G|M)bps", s, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return int(val * 1000) if m.group(2).upper() == "G" else int(val)
    # "NbaseT" / "NGBase-T" patterns
    m = re.search(r"(\d+)\s*[Gg]?[Bb]ase", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Plain integer fallback
    m = re.search(r"(\d+)", s)
    if m:
        return int(m.group(1))
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Layer 1e — Windows Power Plan
# ═════════════════════════════════════════════════════════════════════════════

def _probe_power_plan() -> Dict[str, Any]:
    """Windows only: detect active power scheme and warn if not High Performance."""
    out, _, rc = run_command(["powercfg", "/getactivescheme"])
    result: Dict[str, Any] = {
        "active_scheme":      None,
        "is_high_performance": None,
    }
    if rc == 0 and out:
        result["active_scheme"] = out
        lower = out.lower()
        result["is_high_performance"] = (
            "high performance" in lower or "ultimate performance" in lower
        )
        if not result["is_high_performance"] and "balanced" in lower:
            result["throttle_warning"] = (
                "'Balanced' power plan active — this can throttle NIC and disk "
                "throughput by 10–30%. Switch to High Performance for accurate "
                "benchmarks: powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
            )
    return result
