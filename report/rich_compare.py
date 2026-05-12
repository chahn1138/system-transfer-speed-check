"""
report/rich_compare.py
======================
Rich-formatted multi-host comparison output.
Replaces artifact/aggregate.py print_comparison when `rich` is available.

Falls back automatically to the plain-text version if rich is not installed.
"""

from typing import Any, Dict, List

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    from rich.text import Text
    from rich.rule import Rule
    _RICH = True
except ImportError:
    _RICH = False

from artifact.aggregate import print_comparison as _plain_compare


def print_comparison(aggregate: Dict[str, Any]) -> None:
    if not _RICH:
        _plain_compare(aggregate)
        return
    _rich_compare(aggregate)


# ─────────────────────────────────────────────────────────────────────────────

def _cell(val: Any, *, best_val: Any = None, host: str = None, best_host: str = None,
          star: bool = False, fmt: str = "") -> Any:
    """Format a table cell, highlighting the best value in green."""
    if val is None:
        return Text("—", style="dim")
    s = (fmt % val) if fmt else str(val)
    is_best = (best_host and host == best_host) or (best_val is not None and val == best_val)
    style = "bold green" if is_best else ""
    t = Text(s, style=style)
    if star and is_best:
        t.append(" ★", style="bold green")
    return t


def _rich_compare(aggregate: Dict[str, Any]) -> None:
    console    = Console()
    hosts      = aggregate.get("hosts", {})
    comparison = aggregate.get("comparison", {})

    if not hosts:
        console.print("[red]No host artifacts found.[/]")
        return

    host_names = sorted(hosts.keys())

    console.print()
    console.print(Panel(
        f"[bold cyan]Hosts:[/] {', '.join(host_names)}",
        title="[bold white]MULTI-HOST COMPARISON[/]",
        border_style="cyan",
        padding=(0, 2),
    ))

    # ── Disk ──────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]Disk — Sequential Throughput[/]", style="cyan"))
    disk_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
    disk_table.add_column("Host",           width=28)
    disk_table.add_column("Type/Interface", width=18)
    disk_table.add_column("Read MB/s",      justify="right", width=12)
    disk_table.add_column("Write MB/s",     justify="right", width=12)

    fastest_read  = comparison.get("fastest_disk_read")
    fastest_write = comparison.get("fastest_disk_write")
    for h in host_names:
        d  = comparison.get("disk_speeds", {}).get(h, {})
        dtype = f"{d.get('type') or '?'}/{d.get('interface') or '?'}"
        r  = d.get("read_MBps")
        w  = d.get("write_MBps")
        r_s = _cell(r, host=h, best_host=fastest_read,  star=True, fmt="%.1f")  if r else Text("—", style="dim")
        w_s = _cell(w, host=h, best_host=fastest_write, star=True, fmt="%.1f")  if w else Text("—", style="dim")
        disk_table.add_row(h, dtype, r_s, w_s)
    console.print(disk_table)

    # ── CPU ───────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]CPU[/]", style="cyan"))
    cpu_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    cpu_table.add_column("host",  width=28)
    cpu_table.add_column("info")
    for h in host_names:
        c   = comparison.get("cpu_info", {}).get(h, {})
        aes = c.get("aes_ni")
        aes_str = "[green]✓ AES-NI[/]" if aes is True else ("[red]✗ No AES-NI[/]" if aes is False else "?")
        cpu_table.add_row(
            f"[bold]{h}[/]",
            f"{c.get('model') or '?'}   {c.get('cores_physical')}p / {c.get('cores_logical')}t   {aes_str}",
        )
    console.print(cpu_table)

    # ── RAM ───────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]RAM[/]", style="cyan"))
    ram_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    ram_table.add_column("host", width=28)
    ram_table.add_column("info")
    for h in host_names:
        r = comparison.get("ram_info", {}).get(h, {})
        ram_table.add_row(f"[bold]{h}[/]", f"{r.get('total_GB', '?')} GB")
    console.print(ram_table)

    # ── NICs ──────────────────────────────────────────────────────────────────
    console.print(Rule("[bold]NICs (active)[/]", style="cyan"))
    nic_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    nic_table.add_column("host",  width=28)
    nic_table.add_column("name",  width=32)
    nic_table.add_column("speed", justify="right", width=14)
    for h in host_names:
        nic_entry = comparison.get("nic_speeds", {}).get(h, {})
        nics = nic_entry.get("interfaces", []) if isinstance(nic_entry, dict) else nic_entry
        fn   = nic_entry.get("filtered_note") if isinstance(nic_entry, dict) else None
        first = True
        for n in (nics or []):
            spd = n.get("speed")
            spd_s = f"[bold]{spd:,} Mbps[/]" if spd else "[dim]speed unknown[/]"
            nic_table.add_row(f"[bold]{h}[/]" if first else "", n.get("name", "?"), spd_s)
            first = False
        if not nics:
            nic_table.add_row(f"[bold]{h}[/]", "[dim](none found)[/]", "")
        if fn:
            nic_table.add_row("", f"[dim]↳ {fn}[/]", "")
    console.print(nic_table)

    # ── Layer 5: Live Telemetry ────────────────────────────────────────────────
    live_summary = comparison.get("live_summary", {})
    all_scenarios: List[str] = []
    for host_live in live_summary.values():
        for s in host_live:
            if s not in all_scenarios:
                all_scenarios.append(s)
    if all_scenarios:
        console.print(Rule("[bold]Layer 5 — Live Telemetry (most recent per scenario)[/]", style="cyan"))
        l_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        l_table.add_column("Scenario", width=26)
        for h in host_names:
            l_table.add_column(h, justify="right", min_width=20)

        for scenario in all_scenarios:
            # find best mbps across hosts for this scenario
            best_mbps = None
            for h in host_names:
                r = live_summary.get(h, {}).get(scenario)
                if r and not r.get("error") and r.get("throughput_MBps") is not None:
                    v = r["throughput_MBps"]
                    if best_mbps is None or v > best_mbps:
                        best_mbps = v
            row: List[Any] = [scenario]
            for h in host_names:
                r = live_summary.get(h, {}).get(scenario)
                if not r or r.get("error"):
                    row.append(Text("—", style="dim"))
                    continue
                mbps   = r.get("throughput_MBps")
                tel    = r.get("telemetry") or {}
                cpu_pk = tel.get("cpu_pct_peak")
                mbps_s = f"{mbps:.0f} MB/s" if mbps is not None else "?"
                cpu_s  = f" cpu={cpu_pk}%" if cpu_pk is not None else ""
                is_best = mbps is not None and mbps == best_mbps and len(host_names) > 1
                style   = "bold green" if is_best else ""
                star    = " ★" if is_best else ""
                cell    = Text(f"{mbps_s}{cpu_s}{star}", style=style)
                row.append(cell)
            l_table.add_row(*row)
        console.print(l_table)

    # ── Layer 4: Tuning Sweeps ─────────────────────────────────────────────────
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
        console.print(Rule("[bold]Layer 4 — Tuning Sweeps (best result per sweep)[/]", style="cyan"))
        t_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        t_table.add_column("Sweep", width=16)
        for h in host_names:
            t_table.add_column(h, min_width=22)

        for sweep in all_sweeps:
            label = sweep_labels.get(sweep, sweep)
            # find best mbps for this sweep across hosts
            best_mbps = None
            for h in host_names:
                entry = (tuning_summary.get(h) or {}).get(sweep)
                if entry and entry.get("best_MBps"):
                    v = entry["best_MBps"]
                    if best_mbps is None or v > best_mbps:
                        best_mbps = v
            row: List[Any] = [label]
            for h in host_names:
                entry = (tuning_summary.get(h) or {}).get(sweep)
                if not entry:
                    row.append(Text("—", style="dim"))
                    continue
                mbps    = entry["best_MBps"]
                val     = entry["best_value"]
                is_best = mbps == best_mbps and len(host_names) > 1
                style   = "bold green" if is_best else ""
                star    = " ★" if is_best else ""
                cell    = Text(f"{val} @ {mbps:.0f} MB/s{star}", style=style)
                row.append(cell)
            t_table.add_row(*row)
        console.print(t_table)

    # ── Layer 3: Protocol Benchmarks ──────────────────────────────────────────
    proto_bench = comparison.get("proto_bench", {})
    all_protocols: List[str] = []
    for host_protos in proto_bench.values():
        for p in host_protos:
            if p not in all_protocols:
                all_protocols.append(p)

    if all_protocols:
        console.print(Rule("[bold]Layer 3 — Protocol Benchmarks (most recent per protocol)[/]", style="cyan"))
        p_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        p_table.add_column("Protocol",  width=18)
        p_table.add_column("Dir",       width=7)
        for h in host_names:
            p_table.add_column(h, justify="right", min_width=18)

        for proto in all_protocols:
            direction = "?"
            for h in host_names:
                r = proto_bench.get(h, {}).get(proto)
                if r:
                    direction = r.get("direction", "?")
                    break
            # find best
            best_mbps = None
            best_host = None
            for h in host_names:
                r = proto_bench.get(h, {}).get(proto)
                if r and r.get("throughput_MBps") is not None:
                    v = r["throughput_MBps"]
                    if best_mbps is None or v > best_mbps:
                        best_mbps = v
                        best_host = h
            row: List[Any] = [proto, direction]
            for h in host_names:
                r = proto_bench.get(h, {}).get(proto)
                if not r:
                    row.append(Text("—", style="dim"))
                elif r.get("error"):
                    row.append(Text("ERROR", style="red"))
                elif r.get("throughput_MBps") is not None:
                    mbps    = r["throughput_MBps"]
                    is_best = h == best_host and len(host_names) > 1
                    style   = "bold green" if is_best else ""
                    star    = " ★" if is_best else ""
                    row.append(Text(f"{mbps:.1f} MB/s{star}", style=style))
                else:
                    row.append(Text("—", style="dim"))
            p_table.add_row(*row)
        console.print(p_table)

    # ── Bottleneck Hints ──────────────────────────────────────────────────────
    all_hints = []
    for h in host_names:
        for hint in hosts[h].get("bottleneck_hints", []):
            all_hints.append((h, hint))
    if all_hints:
        console.print(Rule("[bold]Bottleneck Hints[/]", style="yellow"))
        for h, hint in all_hints:
            conf   = (hint.get("confidence") or "?").upper()
            layer  = hint.get("layer") or "?"
            obs    = hint.get("observation") or ""
            action = hint.get("suggested_action") or ""
            conf_style = "bold red" if conf == "HIGH" else ("bold yellow" if conf == "MEDIUM" else "dim")
            body = f"[{conf_style}][{conf}][/] [bold]{h} / {layer}[/]\n{obs}"
            if action:
                body += f"\n[dim]→ {action}[/]"
            console.print(Panel(body, border_style="yellow", padding=(0, 2)))

    console.print()
