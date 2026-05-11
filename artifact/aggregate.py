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


def _latest_per_protocol(results: list) -> dict:
    """Return most recent result dict keyed by protocol name."""
    seen = {}
    for r in results:
        seen[r.get("protocol")] = r   # last write wins
    return seen


def _compare_hosts(hosts: Dict[str, Any]) -> Dict[str, Any]:
    """Derive cross-host comparisons from the loaded artifacts."""
    disk_speeds  = {}
    cpu_info     = {}
    ram_info     = {}
    nic_speeds   = {}
    proto_bench  = {}

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

        proto_results = artifact.get("protocol_results", [])
        proto_bench[hostname] = _latest_per_protocol(proto_results)

    # Collect tuning sweep best-per-sweep per host
    tuning_summary: Dict[str, Any] = {}
    for hostname, artifact in hosts.items():
        tuning_results = artifact.get("tuning_results", [])
        host_tuning: Dict[str, Any] = {}
        for sweep in ("block_size", "thread_count", "compression", "sync_mode", "file_profile"):
            ok = [r for r in tuning_results if r.get("sweep") == sweep
                  and r.get("throughput_MBps") and not r.get("error")]
            if ok:
                best = max(ok, key=lambda r: r["throughput_MBps"])
                host_tuning[sweep] = {
                    "best_value":      best["value"],
                    "best_MBps":       best["throughput_MBps"],
                }
        tuning_summary[hostname] = host_tuning

    # Live telemetry summary per host (most recent of each scenario)
    live_summary: Dict[str, Any] = {}
    for hostname, artifact in hosts.items():
        live_results = artifact.get("live_results", [])
        seen: Dict[str, Any] = {}
        for r in live_results:
            seen[r.get("scenario", "?")] = r
        live_summary[hostname] = seen

    # Find fastest disk per metric across all hosts
    fastest_read  = _find_best(disk_speeds, "read_MBps",  higher_is_better=True)
    fastest_write = _find_best(disk_speeds, "write_MBps", higher_is_better=True)

    return {
        "disk_speeds":        disk_speeds,
        "cpu_info":           cpu_info,
        "ram_info":           ram_info,
        "nic_speeds":         nic_speeds,
        "proto_bench":        proto_bench,
        "tuning_summary":     tuning_summary,
        "live_summary":       live_summary,
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

    # ── Live Telemetry ───────────────────────────────────────────────────────
    live_summary = comparison.get("live_summary", {})
    all_scenarios: List[str] = []
    for host_live in live_summary.values():
        for s in host_live:
            if s not in all_scenarios:
                all_scenarios.append(s)
    if all_scenarios:
        print(f"\n{_LINE}")
        print("  Layer 5 — Live Telemetry (most recent per scenario)")
        print(_LINE)
        col_w  = max(14, max(len(h) for h in host_names) + 2)
        header = f"  {'Scenario':<26}"
        for h in host_names:
            header += f"  {h[:col_w]:<{col_w}}"
        print(header)
        print(f"  {'-'*26}" + f"  {'-'*col_w}" * len(host_names))
        for scenario in all_scenarios:
            row = f"  {scenario:<26}"
            for h in host_names:
                r = live_summary.get(h, {}).get(scenario)
                if not r or r.get("error"):
                    cell = "—"
                else:
                    mbps    = r.get("throughput_MBps")
                    tel     = r.get("telemetry") or {}
                    cpu_pk  = tel.get("cpu_pct_peak")
                    cell = f"{mbps:.0f} MB/s cpu={cpu_pk}%" if mbps and cpu_pk else "—"
                row += f"  {cell:<{col_w}}"
            print(row)

    # ── Tuning Sweeps ─────────────────────────────────────────────────────────
    tuning_summary = comparison.get("tuning_summary", {})
    all_sweeps = ["block_size", "thread_count", "compression", "sync_mode", "file_profile"]
    sweep_labels = {
        "block_size":   "Block Size",
        "thread_count": "Thread Count",
        "compression":  "Compression",
        "sync_mode":    "Sync Mode",
        "file_profile": "File Profile",
    }
    has_tuning = any(tuning_summary.get(h) for h in host_names)
    if has_tuning:
        print(f"\n{_LINE}")
        print("  Layer 4 — Tuning Sweeps (best result per sweep)")
        print(_LINE)
        col_w  = max(14, max(len(h) for h in host_names) + 2)
        header = f"  {'Sweep':<18}"
        for h in host_names:
            header += f"  {h[:col_w]:<{col_w}}"
        print(header)
        print(f"  {'-'*18}" + f"  {'-'*col_w}" * len(host_names))
        for sweep in all_sweeps:
            label = sweep_labels.get(sweep, sweep)
            row   = f"  {label:<18}"
            for h in host_names:
                entry = (tuning_summary.get(h) or {}).get(sweep)
                if entry:
                    cell = f"{entry['best_value']} @ {entry['best_MBps']:.0f} MB/s"
                else:
                    cell = "—"
                row += f"  {cell:<{col_w}}"
            print(row)

    # ── Protocol Benchmarks ───────────────────────────────────────────────────
    proto_bench = comparison.get("proto_bench", {})
    # Collect all protocol names seen across any host
    all_protocols: List[str] = []
    for host_protos in proto_bench.values():
        for p in host_protos:
            if p not in all_protocols:
                all_protocols.append(p)

    if all_protocols:
        print(f"\n{_LINE}")
        print("  Layer 3 — Protocol Benchmarks (most recent run per protocol)")
        print(_LINE)
        col_w = max(14, max(len(h) for h in host_names) + 2)
        header = f"  {'Protocol':<18} {'Dir':<7}"
        for h in host_names:
            header += f"  {h[:col_w]:<{col_w}}"
        print(header)
        print(f"  {'-'*18} {'-'*7}" + f"  {'-'*col_w}" * len(host_names))
        for proto in all_protocols:
            # Determine direction from any host that has it
            direction = "?"
            for h in host_names:
                r = proto_bench.get(h, {}).get(proto)
                if r:
                    direction = r.get("direction", "?")
                    break
            row = f"  {proto:<18} {direction:<7}"
            best_mbps = None
            best_host = None
            for h in host_names:
                r = proto_bench.get(h, {}).get(proto)
                if r and r.get("throughput_MBps") is not None:
                    if best_mbps is None or r["throughput_MBps"] > best_mbps:
                        best_mbps = r["throughput_MBps"]
                        best_host = h
            for h in host_names:
                r = proto_bench.get(h, {}).get(proto)
                if not r:
                    cell = "—"
                elif r.get("error"):
                    cell = "ERROR"
                elif r.get("throughput_MBps") is not None:
                    flag = " ★" if h == best_host and len(host_names) > 1 else ""
                    cell = f"{r['throughput_MBps']:.1f} MB/s{flag}"
                else:
                    cell = "—"
                row += f"  {cell:<{col_w}}"
            print(row)

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
