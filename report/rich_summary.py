"""
report/rich_summary.py
======================
Rich-formatted console summary of a single host's artifact.
Replaces report/summarize.py when `rich` is available.

Falls back automatically to the plain-text version if rich is not installed.
"""

from typing import Any, Dict

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich import box
    from rich.text import Text
    from rich.rule import Rule
    _RICH = True
except ImportError:
    _RICH = False

from report.summarize import print_summary as _plain_summary


def print_summary(artifact: Dict[str, Any]) -> None:
    if not _RICH:
        _plain_summary(artifact)
        return
    _rich_summary(artifact)


# ─────────────────────────────────────────────────────────────────────────────

def _mbps(val: Any, warn_below: float = 0.0, good_above: float = 0.0) -> Any:
    if val is None:
        return Text("—", style="dim")
    s = f"{val:,.1f} MB/s"
    if good_above and val >= good_above:
        return Text(s, style="bold green")
    if warn_below and val < warn_below:
        return Text(s, style="bold yellow")
    return Text(s)


def _mbps_bar(val: float, peak: float, width: int = 20) -> str:
    """Return a simple block-character bar relative to peak."""
    if not peak:
        return ""
    filled = int(round(val / peak * width))
    filled = max(0, min(filled, width))
    return "█" * filled + "░" * (width - filled)


def _rich_summary(artifact: Dict[str, Any]) -> None:
    console = Console()
    host    = artifact.get("host", {})

    # ── Header ────────────────────────────────────────────────────────────────
    title = Text("SYSTEM TRANSFER SPEED CHECK", style="bold white")
    subtitle = (
        f"[bold cyan]{host.get('hostname', 'Unknown')}[/]  ·  "
        f"{host.get('os', '?')} {host.get('os_version', '')[:40]}  ·  "
        f"Python {host.get('python_version', '?')}"
    )
    console.print()
    console.print(Panel(subtitle, title=title, border_style="cyan", padding=(0, 2)))

    # ── Layer 1: Hardware ─────────────────────────────────────────────────────
    hw = artifact.get("hardware_baseline", {})
    if hw:
        console.print(Rule("[bold]Layer 1 — Hardware Baseline[/]", style="cyan"))

        disk = hw.get("disk", {})
        cpu  = hw.get("cpu", {})
        ram  = hw.get("ram", {})
        nic  = hw.get("nic", {})

        hw_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        hw_table.add_column("key",   style="dim",  width=14)
        hw_table.add_column("value", no_wrap=False)

        if disk:
            rd  = disk.get("sequential_read_MBps")
            wr  = disk.get("sequential_write_MBps")
            peak = max(v for v in [rd, wr] if v) if any([rd, wr]) else 1
            hw_table.add_row("Disk type",  f"{disk.get('device_type') or '?'} / {disk.get('interface') or '?'}")
            if rd:
                hw_table.add_row("Seq Read",   f"[green]{rd:,.1f} MB/s[/]  [dim]{_mbps_bar(rd, peak)}[/]")
            if wr:
                hw_table.add_row("Seq Write",  f"[yellow]{wr:,.1f} MB/s[/]  [dim]{_mbps_bar(wr, peak)}[/]")
            hw_table.add_row("Test size",  f"{disk.get('test_file_size_MB', '?')} MB  (method: {disk.get('test_method', '?')})")

        if cpu:
            aes = cpu.get("aes_ni")
            if aes is True:
                aes_str = "[green]✓ AES-NI[/] (hardware crypto)"
            elif aes is False:
                aes_str = "[yellow]✗ No AES-NI[/] (SSH/SCP will be CPU-bound)"
            else:
                aes_str = "AES-NI unknown"
            hw_table.add_row("CPU",   f"{cpu.get('model') or '?'}")
            hw_table.add_row("Cores", f"{cpu.get('cores_physical')}p / {cpu.get('cores_logical')}t   {aes_str}")

        if ram:
            hw_table.add_row("RAM", f"{ram.get('total_GB', '?')} GB")

        ifaces = nic.get("interfaces", []) if nic else []
        for i in ifaces:
            spd  = i.get("negotiated_speed_Mbps")
            name = i.get("name") or i.get("device") or "?"
            spd_str = f"[bold]{spd:,} Mbps[/]" if spd else "[dim]speed unknown[/]"
            fd = i.get("full_duplex")
            fd_str = " full-duplex" if fd else (" half-duplex" if fd is False else "")
            hw_table.add_row("NIC", f"{name}  {spd_str}{fd_str}")

        pp = hw.get("power_plan")
        if pp:
            warn = pp.get("throttle_warning")
            if warn:
                hw_table.add_row("Power", f"[bold yellow]⚠  {warn}[/]")
            elif pp.get("is_high_performance"):
                hw_table.add_row("Power", "[green]✓ High Performance[/]")

        console.print(hw_table)

    # ── Layer 2: Network ──────────────────────────────────────────────────────
    net = artifact.get("network_topology", {})
    if net:
        console.print(Rule("[bold]Layer 2 — Network Characterization[/]", style="cyan"))

        net_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        net_table.add_column("key",   style="dim", width=14)
        net_table.add_column("value")

        gw = net.get("default_gateway")
        if gw:
            net_table.add_row("Gateway", gw)

        ping = net.get("ping", {})
        if ping and ping.get("avg_ms") is not None:
            loss = ping.get("packet_loss_pct", 0)
            loss_str = f"  [bold red]⚠ {loss}% loss[/]" if loss else "  [green]no loss[/]"
            avg = ping["avg_ms"]
            jit = ping.get("jitter_ms", "?")
            avg_style = "green" if avg < 5 else ("yellow" if avg < 30 else "red")
            net_table.add_row("Ping", f"[{avg_style}]avg {avg} ms[/]  jitter {jit} ms{loss_str}  → {ping.get('target', '?')}")

        mtu = net.get("mtu", {})
        if mtu:
            eff = mtu.get("effective_mtu")
            cfg = mtu.get("interface_mtu")
            warn = "  [yellow]⚠ fragmentation risk[/]" if mtu.get("warning") else ""
            net_table.add_row("MTU", f"effective {eff or '?'}  (configured: {cfg or '?'}){warn}")

        wifi = net.get("wifi", {})
        if wifi.get("connected"):
            ssid   = wifi.get("ssid", "?")
            band   = wifi.get("band", "?")
            rssi   = wifi.get("rssi_dBm")
            txrate = wifi.get("tx_rate_Mbps")
            rssi_style = "green" if rssi and rssi >= -60 else ("yellow" if rssi and rssi >= -75 else "red")
            rssi_str   = f"  [{rssi_style}]{rssi} dBm[/]" if rssi else ""
            txrate_str = f"  tx [bold]{txrate} Mbps[/]" if txrate else ""
            net_table.add_row("Wi-Fi", f"[bold]{ssid}[/]  {band}{rssi_str}{txrate_str}")
        elif wifi.get("connected") is False:
            net_table.add_row("Wi-Fi", "[dim]not connected[/]")

        dns = net.get("dns", {})
        avg_dns = dns.get("avg_resolve_ms")
        if avg_dns is not None:
            dns_style = "red" if avg_dns > 100 else ("yellow" if avg_dns > 50 else "green")
            flag = "  [red]⚠ slow![/]" if avg_dns > 100 else ""
            net_table.add_row("DNS", f"[{dns_style}]avg {avg_dns} ms[/]  max {dns.get('max_resolve_ms')} ms{flag}")

        tcp = net.get("tcp_window", {})
        if tcp:
            at = tcp.get("auto_tuning")
            at_str = "[green]enabled ✓[/]" if at else ("[red]DISABLED ⚠[/]" if at is False else "?")
            recv = tcp.get("recv_window_bytes")
            recv_str = f"  recv window {recv//1024} KB" if recv else ""
            net_table.add_row("TCP", f"auto-tuning {at_str}{recv_str}")

        bdp = net.get("bandwidth_delay_product")
        if bdp and bdp.get("bdp_KB"):
            net_table.add_row("BDP", f"{bdp['bdp_KB']} KB  (ideal window for {bdp['nic_speed_Mbps']} Mbps / {bdp['rtt_ms']} ms)")

        hops = (net.get("traceroute") or {}).get("hops", [])
        if hops:
            net_table.add_row("Traceroute", f"{len(hops)} hops to {net.get('traceroute', {}).get('target', '?')}")
            for h in hops[:5]:
                ms = f"{h['avg_ms']} ms" if h.get("avg_ms") else "*"
                net_table.add_row("", f"  hop {h['hop']:2}  {h.get('ip', '*'):<18} {ms}")

        console.print(net_table)

    # ── Layer 5: Live Telemetry ────────────────────────────────────────────────
    live = artifact.get("live_results", [])
    if live:
        console.print(Rule("[bold]Layer 5 — Live Telemetry[/]", style="cyan"))
        live_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        live_table.add_column("Scenario",    width=26)
        live_table.add_column("MB/s",        justify="right", width=10)
        live_table.add_column("CPU peak",    justify="right", width=9)
        live_table.add_column("Disk W peak", justify="right", width=11)
        live_table.add_column("Mem avail min", justify="right", width=13)

        seen: dict = {}
        for r in live:
            seen[r.get("scenario", "?")] = r
        for scenario, r in seen.items():
            if r.get("error"):
                live_table.add_row(scenario, "[red]ERROR[/]", "—", "—", "—")
                continue
            mbps    = r.get("throughput_MBps")
            tel     = r.get("telemetry") or {}
            cpu_pk  = tel.get("cpu_pct_peak")
            dw_pk   = tel.get("disk_write_MBps_peak")
            mem_min = tel.get("mem_available_GB_min")

            mbps_s = f"{mbps:,.1f}" if mbps is not None else "?"
            cpu_s  = f"{cpu_pk:.1f}%" if cpu_pk is not None else "?"
            dw_s   = f"{dw_pk:.1f} MB/s" if dw_pk is not None else "?"
            mem_s  = f"{mem_min:.1f} GB" if mem_min is not None else "?"

            cpu_style = "green" if (cpu_pk or 0) < 25 else ("yellow" if (cpu_pk or 0) < 75 else "red")
            live_table.add_row(
                scenario,
                f"[bold]{mbps_s}[/]",
                f"[{cpu_style}]{cpu_s}[/]",
                dw_s,
                mem_s,
            )
        console.print(live_table)

    # ── Layer 4: Tuning Sweeps ─────────────────────────────────────────────────
    tuning = artifact.get("tuning_results", [])
    if tuning:
        console.print(Rule("[bold]Layer 4 — Tuning Sweeps[/]", style="cyan"))
        t_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        t_table.add_column("Sweep",     width=16)
        t_table.add_column("Best",      width=22)
        t_table.add_column("MB/s",      justify="right", width=10)
        t_table.add_column("Worst",     width=22)
        t_table.add_column("MB/s",      justify="right", width=10)

        sweep_labels = {
            "block_size":   "Block Size",
            "thread_count": "Thread Count",
            "compression":  "Compression",
            "sync_mode":    "Sync Mode",
            "file_profile": "File Profile",
        }
        sweeps: dict = {}
        for r in tuning:
            s = r.get("sweep", "?")
            sweeps.setdefault(s, []).append(r)

        for sweep_key, sweep_results in sweeps.items():
            label = sweep_labels.get(sweep_key, sweep_key)
            ok = [r for r in sweep_results if r.get("throughput_MBps") and not r.get("error")]
            if not ok:
                t_table.add_row(label, "[dim](no results)[/]", "", "", "")
                continue
            best  = max(ok, key=lambda r: r["throughput_MBps"])
            worst = min(ok, key=lambda r: r["throughput_MBps"])
            ratio = best["throughput_MBps"] / worst["throughput_MBps"] if worst["throughput_MBps"] else 1
            gain_str = f"  [dim](×{ratio:.1f} gain)[/]" if ratio > 1.1 else ""
            t_table.add_row(
                label,
                str(best["value"]),
                f"[green]{best['throughput_MBps']:,.1f}[/]{gain_str}",
                str(worst["value"]),
                f"[dim]{worst['throughput_MBps']:,.1f}[/]",
            )
        console.print(t_table)

    # ── Layer 3: Protocol Benchmarks ──────────────────────────────────────────
    proto_results = artifact.get("protocol_results", [])
    if proto_results:
        console.print(Rule("[bold]Layer 3 — Protocol Benchmarks[/]", style="cyan"))
        p_table = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold")
        p_table.add_column("Protocol",  width=18)
        p_table.add_column("Direction", width=9)
        p_table.add_column("Target",    width=18)
        p_table.add_column("MB/s",      justify="right", width=10)
        p_table.add_column("Notes",     width=40)

        seen: dict = {}
        for r in proto_results:
            seen[r.get("protocol")] = r
        # find best for highlighting
        best_mbps = max((r.get("throughput_MBps") or 0) for r in seen.values())
        for proto, r in seen.items():
            direction = r.get("direction", "?")
            tgt       = r.get("target") or "—"
            mbps      = r.get("throughput_MBps")
            err       = r.get("error")
            note      = (err or r.get("notes") or "")[:50]
            if err:
                mbps_s = "[red]ERROR[/]"
                note_s = f"[red]{note}[/]"
            elif mbps is not None:
                is_best = mbps == best_mbps and len(seen) > 1
                style   = "bold green" if is_best else ""
                star    = " ★" if is_best else ""
                mbps_s  = f"[{style}]{mbps:,.1f}{star}[/]" if style else f"{mbps:,.1f}{star}"
                note_s  = note
            else:
                mbps_s = "[dim]—[/]"
                note_s = note
            p_table.add_row(proto, direction, tgt, mbps_s, note_s)
        console.print(p_table)

    # ── Bottleneck Hints ──────────────────────────────────────────────────────
    hints = artifact.get("bottleneck_hints", [])
    if hints:
        console.print(Rule("[bold]Bottleneck Hints[/]", style="yellow"))
        for h in hints:
            conf   = (h.get("confidence") or "?").upper()
            layer  = h.get("layer") or "?"
            obs    = h.get("observation") or ""
            action = h.get("suggested_action") or ""
            conf_style = "bold red" if conf == "HIGH" else ("bold yellow" if conf == "MEDIUM" else "dim")
            body = f"[{conf_style}][{conf}][/] [bold]{layer}[/]\n{obs}"
            if action:
                body += f"\n[dim]→ {action}[/]"
            console.print(Panel(body, border_style="yellow", padding=(0, 2)))

    # ── Run History ───────────────────────────────────────────────────────────
    history = artifact.get("run_history", [])
    if history:
        console.print(Rule("[bold]Run History[/]", style="dim"))
        h_table = Table(box=box.SIMPLE, show_header=True, header_style="dim")
        h_table.add_column("Timestamp",   width=20)
        h_table.add_column("OS",          width=10)
        h_table.add_column("Summary",     width=60)
        for r in history[-5:]:
            h_table.add_row(
                r.get("timestamp", "")[:19] + "Z",
                r.get("os", "?"),
                r.get("summary", ""),
            )
        console.print(h_table)

    console.print()
