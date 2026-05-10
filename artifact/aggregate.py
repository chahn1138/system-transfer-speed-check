"""
artifact/aggregate.py
=====================
Build a side-by-side comparison of all per-host artifacts.

Usage (from run_probes.py --compare):
    from artifact.aggregate import build_aggregate, print_comparison
    agg = build_aggregate("artifact/hosts")
    print_comparison(agg)
"""

import json
import os
from typing import Any, Dict, List, Optional


def build_aggregate(hosts_dir: str) -> Dict[str, Any]:
    """
    Read all *.json files in hosts_dir and return a unified comparison dict.
    """
    hosts: Dict[str, Any] = {}

    if not os.path.isdir(hosts_dir):
        return {"hosts": {}, "comparison": {}}

    for fname in sorted(os.listdir(hosts_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(hosts_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                artifact = json.load(f)
            hostname = artifact.get("host", {}).get("hostname") or fname.replace(".json", "")
            hosts[hostname] = artifact
        except (json.JSONDecodeError, OSError):
            continue

    comparison = _compare_hosts(hosts)
    return {"hosts": hosts, "comparison": comparison}


def _compare_hosts(hosts: Dict[str, Any]) -> Dict[str, Any]:
    """Derive cross-host comparisons from the loaded artifacts."""
    disk_speeds  = {}
    cpu_info     = {}
    ram_info     = {}
    nic_speeds   = {}

    for hostname, artifact in hosts.items():
        hw   = artifact.get("hardware_baseline", {})
        disk = hw.get("disk", {})
        cpu  = hw.get("cpu", {})
        ram  = hw.get("ram", {})
        nic  = hw.get("nic", {})

        disk_speeds[hostname] = {
            "read_MBps":  disk.get("sequential_read_MBps"),
            "write_MBps": disk.get("sequential_write_MBps"),
            "type":       disk.get("device_type"),
            "interface":  disk.get("interface"),
        }
        cpu_info[hostname] = {
            "model":          cpu.get("model"),
            "cores_physical": cpu.get("cores_physical"),
            "cores_logical":  cpu.get("cores_logical"),
            "aes_ni":         cpu.get("aes_ni"),
        }
        ram_info[hostname] = {"total_GB": ram.get("total_GB")}

        ifaces = nic.get("interfaces", [])
        nic_speeds[hostname] = {
            "interfaces": [
                {
                    "name":  i.get("name") or i.get("device"),
                    "speed": i.get("negotiated_speed_Mbps"),
                }
                for i in ifaces
            ],
            "filtered_note": nic.get("filtered_note"),
        }

    # Find fastest disk per metric across all hosts
    fastest_read  = _find_best(disk_speeds, "read_MBps",  higher_is_better=True)
    fastest_write = _find_best(disk_speeds, "write_MBps", higher_is_better=True)

    return {
        "disk_speeds":    disk_speeds,
        "cpu_info":       cpu_info,
        "ram_info":       ram_info,
        "nic_speeds":     nic_speeds,
        "fastest_disk_read":  fastest_read,
        "fastest_disk_write": fastest_write,
    }


def _find_best(
    data: Dict[str, Dict],
    key: str,
    higher_is_better: bool = True,
) -> Optional[str]:
    """Return the hostname with the best value for a given key."""
    candidates = {h: v.get(key) for h, v in data.items() if v.get(key) is not None}
    if not candidates:
        return None
    return max(candidates, key=lambda h: candidates[h]) if higher_is_better \
           else min(candidates, key=lambda h: candidates[h])


def print_comparison(aggregate: Dict[str, Any]) -> None:
    """Print a formatted cross-host comparison to stdout."""
    _SEP  = "═" * 72
    _LINE = "─" * 72

    hosts      = aggregate.get("hosts", {})
    comparison = aggregate.get("comparison", {})

    if not hosts:
        print("No host artifacts found.")
        return

    host_names = sorted(hosts.keys())

    print(f"\n{_SEP}")
    print("   MULTI-HOST COMPARISON")
    print(_SEP)
    print(f"  Hosts in artifact store: {', '.join(host_names)}")

    # ── Disk ─────────────────────────────────────────────────────────────────
    print(f"\n{_LINE}")
    print("  Disk — Sequential Throughput")
    print(_LINE)
    print(f"  {'Host':<28} {'Type/Interface':<18} {'Read MB/s':>10} {'Write MB/s':>11}")
    print(f"  {'-'*28} {'-'*18} {'-'*10} {'-'*11}")
    for h in host_names:
        d = comparison.get("disk_speeds", {}).get(h, {})
        dtype = f"{d.get('type') or '?'}/{d.get('interface') or '?'}"
        r = d.get("read_MBps")
        w = d.get("write_MBps")
        r_str = f"{r:>10,.1f}" if r else f"{'—':>10}"
        w_str = f"{w:>11,.1f}" if w else f"{'—':>11}"
        flag  = " ← fastest read" if h == comparison.get("fastest_disk_read") else ""
        print(f"  {h:<28} {dtype:<18} {r_str} {w_str}{flag}")

    # ── CPU ──────────────────────────────────────────────────────────────────
    print(f"\n{_LINE}")
    print("  CPU")
    print(_LINE)
    for h in host_names:
        c = comparison.get("cpu_info", {}).get(h, {})
        aes = "✓ AES-NI" if c.get("aes_ni") else ("✗ No AES-NI" if c.get("aes_ni") is False else "AES-NI ?")
        print(f"  {h:<28} {c.get('model') or '?'}")
        print(f"  {'':28} {c.get('cores_physical')}p / {c.get('cores_logical')}t   {aes}")

    # ── RAM ──────────────────────────────────────────────────────────────────
    print(f"\n{_LINE}")
    print("  RAM")
    print(_LINE)
    for h in host_names:
        r = comparison.get("ram_info", {}).get(h, {})
        print(f"  {h:<28} {r.get('total_GB', '?')} GB")

    # ── NICs ─────────────────────────────────────────────────────────────────
    print(f"\n{_LINE}")
    print("  NICs (active)")
    print(_LINE)
    for h in host_names:
        nic_entry = comparison.get("nic_speeds", {}).get(h, {})
        nics      = nic_entry.get("interfaces", []) if isinstance(nic_entry, dict) else nic_entry
        fn        = nic_entry.get("filtered_note") if isinstance(nic_entry, dict) else None
        print(f"  {h}:")
        if nics:
            for n in nics:
                spd = n.get("speed")
                spd_str = f"{spd:,} Mbps" if spd else "speed unknown"
                print(f"    · {n.get('name', '?'):<30} {spd_str}")
        else:
            print("    (no active NICs found)")
        if fn:
            print(f"    ↳ {fn}")

    # ── Bottleneck hints ─────────────────────────────────────────────────────
    all_hints = []
    for h in host_names:
        for hint in hosts[h].get("bottleneck_hints", []):
            all_hints.append((h, hint))
    if all_hints:
        print(f"\n{_LINE}")
        print("  Bottleneck Hints (all hosts)")
        print(_LINE)
        for h, hint in all_hints:
            conf  = (hint.get("confidence") or "?").upper()
            layer = hint.get("layer") or "?"
            obs   = hint.get("observation") or ""
            print(f"  [{conf}] {h} / {layer}: {obs}")
            action = hint.get("suggested_action")
            if action:
                print(f"    → {action}")

    print(f"{_SEP}\n")
