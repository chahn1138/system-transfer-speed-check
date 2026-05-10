"""
probe/network.py
================
Layer 2 — Network Characterization

Probes latency, jitter, MTU, NIC link speed, Wi-Fi state, DNS timing,
TCP window tuning, and optional traceroute on Windows, Linux, and macOS.

Public API
----------
    probe_network(target: str = None) -> dict
"""

import re
import socket
import time
from typing import Any, Dict, List, Optional

from .platform_utils import detect_os, run_command, ps, tool_available

_OS = detect_os()

# ── Defaults ──────────────────────────────────────────────────────────────────
_PING_COUNT       = 10          # packets for latency/jitter measurement
_TRACEROUTE_HOPS  = 8           # max hops for lightweight traceroute
_DNS_TEST_HOSTS   = ["github.com", "google.com", "cloudflare.com"]
_MTU_PROBE_SIZES  = [576, 1024, 1280, 1400, 1472, 1500, 4000, 9000]


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def probe_network(target: Optional[str] = None) -> Dict[str, Any]:
    """Run all Layer 2 network probes and return a results dict."""
    gateway = _default_gateway()

    ping_target = target or gateway
    results: Dict[str, Any] = {
        "default_gateway":   gateway,
        "ping":              _probe_ping(ping_target) if ping_target else None,
        "mtu":               _probe_mtu(gateway) if gateway else None,
        "interfaces":        _probe_interfaces(),
        "wifi":              _probe_wifi(),
        "dns":               _probe_dns(),
        "tcp_window":        _probe_tcp_window(),
        "traceroute":        _probe_traceroute(ping_target) if ping_target else None,
    }

    # Derived: Bandwidth-Delay Product (filled in by analysis, not probing)
    rtt = (results["ping"] or {}).get("avg_ms")
    results["bandwidth_delay_product"] = _calc_bdp(results["interfaces"], rtt)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Default gateway
# ═════════════════════════════════════════════════════════════════════════════

def _default_gateway() -> Optional[str]:
    try:
        if _OS == "windows":
            out, _, rc = ps(
                "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' "
                "| Sort-Object RouteMetric | Select-Object -First 1).NextHop"
            )
            return out.strip() if rc == 0 and out.strip() else None

        elif _OS == "linux":
            out, _, rc = run_command(["ip", "route", "show", "default"])
            if rc == 0:
                m = re.search(r"default via (\S+)", out)
                if m:
                    return m.group(1)

        elif _OS == "macos":
            out, _, rc = run_command(["route", "-n", "get", "default"])
            if rc == 0:
                for line in out.splitlines():
                    s = line.strip()
                    if s.startswith("gateway:"):
                        return s.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Ping — latency & jitter
# ═════════════════════════════════════════════════════════════════════════════

def _probe_ping(target: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "target":      target,
        "packets_sent": _PING_COUNT,
        "packet_loss_pct": None,
        "min_ms":  None,
        "avg_ms":  None,
        "max_ms":  None,
        "jitter_ms": None,
    }
    try:
        if _OS == "windows":
            out, _, rc = run_command(
                ["ping", "-n", str(_PING_COUNT), target], timeout=30
            )
            if rc == 0:
                result.update(_parse_ping_windows(out))
        else:
            out, _, rc = run_command(
                ["ping", "-c", str(_PING_COUNT), target], timeout=30
            )
            if rc == 0:
                result.update(_parse_ping_posix(out))
    except Exception as e:
        result["error"] = str(e)
    return result


def _parse_ping_windows(output: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in output.splitlines():
        # "Lost = 0 (0% loss)"
        m = re.search(r"Lost\s*=\s*\d+\s*\((\d+)%\s*loss\)", line, re.IGNORECASE)
        if m:
            data["packet_loss_pct"] = float(m.group(1))
        # "Minimum = 1ms, Maximum = 3ms, Average = 2ms"
        m = re.search(
            r"Minimum\s*=\s*(\d+)ms.*Maximum\s*=\s*(\d+)ms.*Average\s*=\s*(\d+)ms",
            line, re.IGNORECASE,
        )
        if m:
            mn, mx, av = float(m.group(1)), float(m.group(2)), float(m.group(3))
            data["min_ms"]    = mn
            data["max_ms"]    = mx
            data["avg_ms"]    = av
            data["jitter_ms"] = round(mx - mn, 2)
    return data


def _parse_ping_posix(output: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in output.splitlines():
        # "10 packets transmitted, 10 received, 0% packet loss"
        m = re.search(r"(\d+)%\s+packet loss", line)
        if m:
            data["packet_loss_pct"] = float(m.group(1))
        # "rtt min/avg/max/mdev = 0.512/1.023/2.100/0.400 ms"
        m = re.search(
            r"min/avg/max/(?:mdev|stddev)\s*=\s*([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
            line,
        )
        if m:
            data["min_ms"]    = float(m.group(1))
            data["avg_ms"]    = float(m.group(2))
            data["max_ms"]    = float(m.group(3))
            data["jitter_ms"] = float(m.group(4))
    return data


# ═════════════════════════════════════════════════════════════════════════════
# MTU discovery
# ═════════════════════════════════════════════════════════════════════════════

def _probe_mtu(target: str) -> Dict[str, Any]:
    """
    Probe effective MTU by sending progressively larger ping packets with DF bit.
    Returns the largest size that succeeded and a warning if < 1500.
    """
    result: Dict[str, Any] = {
        "target":           target,
        "effective_mtu":    None,
        "interface_mtu":    _interface_mtu(),
        "warning":          None,
    }
    last_good = None
    for size in _MTU_PROBE_SIZES:
        # ICMP payload = size - 28 (20 IP + 8 ICMP headers)
        payload = max(0, size - 28)
        success = _ping_with_size(target, payload)
        if success:
            last_good = size
        else:
            break

    result["effective_mtu"] = last_good
    if last_good is not None and last_good < 1500:
        result["warning"] = (
            f"Effective MTU is {last_good} bytes (< 1500). "
            "Fragmentation is occurring — check switch/router jumbo frame config "
            "or VPN tunnel overhead."
        )
    return result


def _ping_with_size(target: str, payload_bytes: int) -> bool:
    """Send one ping with a specific payload size; return True if it succeeds."""
    try:
        if _OS == "windows":
            _, _, rc = run_command(
                ["ping", "-n", "1", "-l", str(payload_bytes), "-f", target],
                timeout=5,
            )
        elif _OS == "linux":
            _, _, rc = run_command(
                ["ping", "-c", "1", "-M", "do", "-s", str(payload_bytes), target],
                timeout=5,
            )
        else:  # macOS
            _, _, rc = run_command(
                ["ping", "-c", "1", "-D", "-s", str(payload_bytes), target],
                timeout=5,
            )
        return rc == 0
    except Exception:
        return False


def _interface_mtu() -> Optional[int]:
    """Read the configured MTU of the primary interface."""
    try:
        if _OS == "windows":
            out, _, rc = ps(
                "Get-NetIPInterface | Where-Object { $_.ConnectionState -eq 'Connected' } "
                "| Select-Object -First 1 -ExpandProperty NlMtu"
            )
            if rc == 0 and out.strip().isdigit():
                return int(out.strip())

        elif _OS == "linux":
            out, _, rc = run_command(["ip", "-j", "link", "show"])
            if rc == 0:
                import json
                for link in __import__("json").loads(out):
                    if link.get("operstate", "").lower() == "up" and link.get("ifname") != "lo":
                        return link.get("mtu")

        elif _OS == "macos":
            out, _, rc = run_command(["route", "-n", "get", "default"])
            if rc == 0:
                for line in out.splitlines():
                    s = line.strip()
                    if s.startswith("interface:"):
                        dev = s.split(":", 1)[1].strip()
                        ifc_out, _, _ = run_command(["ifconfig", dev])
                        m = re.search(r"\bmtu\s+(\d+)", ifc_out)
                        if m:
                            return int(m.group(1))
    except Exception:
        pass
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Interface enumeration (NIC link speed — cross-platform improved)
# ═════════════════════════════════════════════════════════════════════════════

def _probe_interfaces() -> List[Dict[str, Any]]:
    """Return list of active interface dicts with name, speed, duplex, type."""
    try:
        if _OS == "windows":
            return _interfaces_windows()
        elif _OS == "linux":
            return _interfaces_linux()
        elif _OS == "macos":
            return _interfaces_macos()
    except Exception:
        pass
    return []


def _interfaces_windows() -> List[Dict[str, Any]]:
    import json
    out, _, rc = ps(
        "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | "
        "Select-Object Name, LinkSpeed, FullDuplex, DriverDescription, MediaType | "
        "ConvertTo-Json"
    )
    if rc != 0 or not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "name":                  i.get("Name"),
            "type":                  i.get("MediaType"),
            "negotiated_speed_Mbps": _parse_link_speed(i.get("LinkSpeed", "")),
            "negotiated_speed_str":  i.get("LinkSpeed"),
            "full_duplex":           i.get("FullDuplex"),
            "driver":                i.get("DriverDescription"),
        }
        for i in data
    ]


def _interfaces_linux() -> List[Dict[str, Any]]:
    import json
    out, _, rc = run_command(["ip", "-j", "link", "show"])
    if rc != 0 or not out:
        return []
    links = json.loads(out)
    results = []
    for link in links:
        name = link.get("ifname", "")
        if name == "lo" or link.get("operstate", "").lower() != "up":
            continue
        entry: Dict[str, Any] = {
            "name":                  name,
            "type":                  link.get("link_type"),
            "negotiated_speed_Mbps": None,
            "full_duplex":           None,
        }
        if tool_available("ethtool"):
            eth_out, _, _ = run_command(["ethtool", name], timeout=5)
            for line in eth_out.splitlines():
                s = line.strip()
                if s.startswith("Speed:"):
                    entry["negotiated_speed_Mbps"] = _parse_link_speed(s)
                if s.startswith("Duplex:"):
                    entry["full_duplex"] = "full" in s.lower()
        results.append(entry)
    return results


def _interfaces_macos() -> List[Dict[str, Any]]:
    """
    Enumerate physical network interfaces on macOS.

    macOS exposes a large number of virtual/tunnel interfaces (utun*, awdl0,
    llw0, anpi*, vmenet*, bridge*, ap1, etc.) that are irrelevant to transfer
    performance benchmarking. We keep only interfaces whose device name matches
    known physical patterns (en*, bridge for real bridges) AND that carry a
    recognisable media line in ifconfig output.

    Filtered-out interfaces are counted and recorded in a 'filtered_note' field
    on the returned dict so the artifact always shows what was excluded and why.
    No silent discards.
    """
    import json as _json

    # ── Prefixes / names that are always virtual on macOS ────────────────────
    # utun*  : VPN tunnels (WireGuard, OpenVPN, macOS VPN framework)
    # awdl0  : Apple Wireless Direct Link (AirDrop / Sidecar)
    # llw0   : Low-latency WLAN (companion to awdl0)
    # anpi*  : Apple Network Protocol Interface (internal firmware comms)
    # vmenet*: Parallels / VMware virtual ethernet
    # ap1    : Access-point mode virtual interface
    # lo0    : loopback
    VIRTUAL_PREFIXES = ("utun", "awdl", "llw", "anpi", "vmenet", "lo", "ap")
    VIRTUAL_EXACT    = {"ap1"}

    def _is_virtual(dev: str) -> bool:
        return dev in VIRTUAL_EXACT or any(dev.startswith(p) for p in VIRTUAL_PREFIXES)

    # Primary source: system_profiler gives human-readable port names
    sp_out, _, sp_rc = run_command(
        ["system_profiler", "SPNetworkDataType", "-json"], timeout=15
    )
    sp_ifaces: Dict[str, Dict] = {}
    if sp_rc == 0 and sp_out:
        try:
            sp_data = _json.loads(sp_out)
            for entry in sp_data.get("SPNetworkDataType", []):
                dev = entry.get("interface", "")
                if dev:
                    sp_ifaces[dev] = entry
        except (_json.JSONDecodeError, KeyError):
            pass

    # Parse ifconfig -u (UP interfaces only) into per-device blocks
    ifc_out, _, _ = run_command(["ifconfig", "-u"])
    if not ifc_out:
        ifc_out, _, _ = run_command(["ifconfig"])

    iface_blocks: Dict[str, List[str]] = {}
    current_dev = None
    for line in ifc_out.splitlines():
        m = re.match(r"^(\w[\w.]+):", line)
        if m:
            current_dev = m.group(1)
            iface_blocks[current_dev] = [line]
        elif current_dev and line.startswith("\t"):
            iface_blocks[current_dev].append(line)

    results      = []
    skipped_devs = []   # track what we filtered for the note

    for dev, lines in iface_blocks.items():
        block = "\n".join(lines)
        if "LOOPBACK" in block:
            skipped_devs.append(f"{dev}(loopback)")
            continue
        if _is_virtual(dev):
            skipped_devs.append(f"{dev}(virtual)")
            continue

        speed_mbps  = None
        full_duplex = None
        iface_type  = None

        # Parse: "	media: autoselect (1000baseT <full-duplex>)"
        media_m = re.search(r"media:\s+\S+\s*\(([^)]*)\)", block)
        if media_m:
            media_str   = media_m.group(1)
            speed_mbps  = _parse_link_speed(media_str)
            full_duplex = "full-duplex" in media_str.lower()

        sp   = sp_ifaces.get(dev, {})
        name = sp.get("_name") or dev

        lower_name = name.lower()
        if "wi-fi" in lower_name or "airport" in lower_name:
            iface_type = "Wi-Fi"
        elif "thunderbolt" in lower_name:
            iface_type = "Thunderbolt"
        elif "bluetooth" in lower_name:
            iface_type = "Bluetooth"
        elif "ethernet" in lower_name or dev.startswith("en"):
            iface_type = "Ethernet"
        elif dev.startswith("bridge"):
            iface_type = "Bridge"

        results.append({
            "name":                  name,
            "device":                dev,
            "type":                  iface_type,
            "negotiated_speed_Mbps": speed_mbps,
            "full_duplex":           full_duplex,
            "ip_address":            (sp.get("ip_address") or [None])[0],
        })

    # Always record what was filtered and why — never a silent discard
    filtered_note = (
        f"Filtered out {len(skipped_devs)} virtual/loopback interface(s): "
        + ", ".join(skipped_devs)
    ) if skipped_devs else "No virtual interfaces filtered."

    return {"interfaces": results, "filtered_note": filtered_note}


# ═════════════════════════════════════════════════════════════════════════════
# Wi-Fi details
# ═════════════════════════════════════════════════════════════════════════════

def _probe_wifi() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "connected": None,
        "ssid":      None,
        "band":      None,
        "channel":   None,
        "rssi_dBm":  None,
        "standard":  None,
        "tx_rate_Mbps": None,
    }
    try:
        if _OS == "windows":
            result.update(_wifi_windows())
        elif _OS == "linux":
            result.update(_wifi_linux())
        elif _OS == "macos":
            result.update(_wifi_macos())
    except Exception as e:
        result["error"] = str(e)
    return result


def _wifi_windows() -> Dict[str, Any]:
    out, _, rc = run_command(["netsh", "wlan", "show", "interfaces"])
    if rc != 0 or not out:
        return {"connected": False}
    data: Dict[str, Any] = {"connected": False}
    for line in out.splitlines():
        s = line.strip()
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        k, v = k.strip().lower(), v.strip()
        if "state" in k:
            data["connected"] = "connected" in v.lower()
        elif "ssid" in k and "bssid" not in k:
            data["ssid"] = v
        elif "band" in k:
            data["band"] = v
        elif "channel" in k:
            data["channel"] = _safe_int(v)
        elif "signal" in k:
            # Windows gives % not dBm; annotate clearly
            data["signal_pct"] = _safe_int(v.replace("%", ""))
        elif "receive rate" in k or "rx rate" in k:
            data["tx_rate_Mbps"] = _safe_float(v.split()[0])
        elif "radio type" in k:
            data["standard"] = v
    return data


def _wifi_linux() -> Dict[str, Any]:
    if tool_available("iw"):
        out, _, rc = run_command(["iw", "dev"])
        if rc == 0:
            # find first wlan interface
            for line in out.splitlines():
                m = re.search(r"Interface\s+(\w+)", line)
                if m:
                    dev = m.group(1)
                    link_out, _, _ = run_command(["iw", "dev", dev, "link"])
                    return _parse_iw_link(link_out)
    if tool_available("iwconfig"):
        out, _, rc = run_command(["iwconfig"])
        if rc == 0:
            return _parse_iwconfig(out)
    return {"connected": False}


def _parse_iw_link(output: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"connected": "Not connected" not in output}
    for line in output.splitlines():
        s = line.strip()
        if s.startswith("SSID:"):
            data["ssid"] = s.split(":", 1)[1].strip()
        m = re.search(r"signal:\s*([-\d]+)\s*dBm", s)
        if m:
            data["rssi_dBm"] = int(m.group(1))
        m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit", s, re.IGNORECASE)
        if m:
            data["tx_rate_Mbps"] = float(m.group(1))
        m = re.search(r"channel\s+(\d+)", s, re.IGNORECASE)
        if m:
            ch = int(m.group(1))
            data["channel"] = ch
            data["band"] = "5 GHz" if ch > 14 else "2.4 GHz"
    return data


def _parse_iwconfig(output: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"connected": False}
    m = re.search(r'ESSID:"([^"]+)"', output)
    if m:
        data["ssid"]      = m.group(1)
        data["connected"] = True
    m = re.search(r"Signal level[=:]\s*([-\d]+)\s*dBm", output)
    if m:
        data["rssi_dBm"] = int(m.group(1))
    m = re.search(r"Bit Rate[=:]\s*([\d.]+)\s*Mb", output, re.IGNORECASE)
    if m:
        data["tx_rate_Mbps"] = float(m.group(1))
    return data


def _wifi_macos() -> Dict[str, Any]:
    airport = (
        "/System/Library/PrivateFrameworks/Apple80211.framework"
        "/Versions/Current/Resources/airport"
    )
    out, _, rc = run_command([airport, "-I"], timeout=10)
    if rc != 0 or "AirPort: Off" in out:
        return {"connected": False}
    data: Dict[str, Any] = {"connected": False}
    for line in out.splitlines():
        s = line.strip()
        if ":" not in s:
            continue
        k, _, v = s.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "ssid":
            data["ssid"]      = v
            data["connected"] = True
        elif k == "agrctlrssi":
            try:
                data["rssi_dBm"] = int(v)
            except ValueError:
                pass
        elif k == "channel":
            # e.g. "6,+1" or "36,80"
            ch_str = v.split(",")[0]
            try:
                ch = int(ch_str)
                data["channel"] = ch
                data["band"]    = "5 GHz" if ch > 14 else "2.4 GHz"
            except ValueError:
                pass
        elif k == "lastassocstatus":
            pass
        elif k == "op mode":
            data["standard"] = v
        elif "maxrate" in k or "lastrate" in k:
            try:
                data["tx_rate_Mbps"] = float(v)
            except ValueError:
                pass
    return data


# ═════════════════════════════════════════════════════════════════════════════
# DNS resolution timing
# ═════════════════════════════════════════════════════════════════════════════

def _probe_dns() -> Dict[str, Any]:
    results = []
    for host in _DNS_TEST_HOSTS:
        t0 = time.perf_counter()
        try:
            socket.getaddrinfo(host, 80)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
            results.append({"host": host, "resolve_ms": elapsed_ms, "success": True})
        except socket.gaierror as e:
            results.append({"host": host, "resolve_ms": None, "success": False, "error": str(e)})

    successful = [r["resolve_ms"] for r in results if r["success"] and r["resolve_ms"] is not None]
    return {
        "samples":         results,
        "avg_resolve_ms":  round(sum(successful) / len(successful), 2) if successful else None,
        "max_resolve_ms":  max(successful) if successful else None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# TCP window tuning
# ═════════════════════════════════════════════════════════════════════════════

def _probe_tcp_window() -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "auto_tuning":        None,
        "recv_window_bytes":  None,
        "send_window_bytes":  None,
        "notes":              None,
    }
    try:
        if _OS == "windows":
            out, _, rc = run_command(["netsh", "int", "tcp", "show", "global"])
            if rc == 0:
                for line in out.splitlines():
                    s = line.strip().lower()
                    if "receive window auto-tuning" in s:
                        result["auto_tuning"] = "disabled" not in s
                        if not result["auto_tuning"]:
                            result["notes"] = (
                                "TCP receive window auto-tuning is DISABLED. "
                                "High-bandwidth paths will be throttled. "
                                "Enable: netsh int tcp set global autotuninglevel=normal"
                            )

        elif _OS == "linux":
            for key, field in [
                ("net.core.rmem_max", "recv_window_bytes"),
                ("net.core.wmem_max", "send_window_bytes"),
            ]:
                out, _, rc = run_command(["sysctl", "-n", key])
                if rc == 0 and out.strip().isdigit():
                    result[field] = int(out.strip())
            out, _, rc = run_command(["sysctl", "-n", "net.ipv4.tcp_window_scaling"])
            if rc == 0:
                result["auto_tuning"] = out.strip() == "1"

        elif _OS == "macos":
            for key, field in [
                ("net.inet.tcp.recvspace", "recv_window_bytes"),
                ("net.inet.tcp.sendspace", "send_window_bytes"),
            ]:
                out, _, rc = run_command(["sysctl", "-n", key])
                if rc == 0:
                    try:
                        result[field] = int(out.strip())
                    except ValueError:
                        pass
            out, _, rc = run_command(["sysctl", "-n", "net.inet.tcp.rfc1323"])
            if rc == 0:
                result["auto_tuning"] = out.strip() == "1"

    except Exception as e:
        result["error"] = str(e)

    return result


# ═════════════════════════════════════════════════════════════════════════════
# Traceroute (lightweight — first N hops only)
# ═════════════════════════════════════════════════════════════════════════════

def _probe_traceroute(target: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"target": target, "hops": [], "error": None}
    try:
        if _OS == "windows":
            out, _, rc = run_command(
                ["tracert", "-h", str(_TRACEROUTE_HOPS), "-d", target], timeout=30
            )
        else:
            cmd = "traceroute" if tool_available("traceroute") else "tracepath"
            flags = ["-m", str(_TRACEROUTE_HOPS), "-n"]
            out, _, rc = run_command([cmd] + flags + [target], timeout=30)

        if rc == 0 and out:
            result["hops"] = _parse_traceroute(out)
    except Exception as e:
        result["error"] = str(e)
    return result


def _parse_traceroute(output: str) -> List[Dict[str, Any]]:
    hops = []
    for line in output.splitlines():
        # Match lines starting with hop number (1-99)
        m = re.match(r"\s*(\d+)\s+", line)
        if not m:
            continue
        hop_num = int(m.group(1))
        # Find all IP addresses in the line
        ips = re.findall(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
        # Find all timing values (ms)
        times = re.findall(r"([\d.]+)\s*ms", line)
        avg_ms = round(sum(float(t) for t in times) / len(times), 2) if times else None
        hops.append({
            "hop":    hop_num,
            "ip":     ips[0] if ips else "*",
            "avg_ms": avg_ms,
        })
    return hops


# ═════════════════════════════════════════════════════════════════════════════
# Bandwidth-Delay Product (derived)
# ═════════════════════════════════════════════════════════════════════════════

def _calc_bdp(interfaces: List[Dict], rtt_ms: Optional[float]) -> Optional[Dict[str, Any]]:
    """
    BDP (bytes) = bandwidth (bps) × RTT (s) / 8
    This is the ideal TCP window size for a given path.
    """
    if not interfaces or rtt_ms is None:
        return None
    # Use the fastest NIC's speed
    speeds = [i.get("negotiated_speed_Mbps") for i in interfaces if i.get("negotiated_speed_Mbps")]
    if not speeds:
        return None
    best_mbps = max(speeds)
    bdp_bytes = int(best_mbps * 1_000_000 / 8 * (rtt_ms / 1000))
    return {
        "nic_speed_Mbps":       best_mbps,
        "rtt_ms":               rtt_ms,
        "bdp_bytes":            bdp_bytes,
        "bdp_KB":               round(bdp_bytes / 1024, 1),
        "note": (
            f"Ideal TCP window for this path is {round(bdp_bytes/1024, 1)} KB. "
            "If your TCP window (see tcp_window) is smaller, throughput will be limited."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _parse_link_speed(s: str) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(G|M)bps", s, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return int(val * 1000) if m.group(2).upper() == "G" else int(val)
    m = re.search(r"(\d+)\s*[Gg]?[Bb]ase", s, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", s)
    if m:
        return int(m.group(1))
    return None


def _safe_int(s: str) -> Optional[int]:
    try:
        return int(re.search(r"\d+", s).group())
    except (AttributeError, ValueError):
        return None


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except (ValueError, TypeError):
        return None
