"""
report/summarize.py
===================
Human-readable console summary of a single host's transfer-profile artifact.

Intentionally uses only stdlib so it works before 'pip install' is run.
A rich-based version will be added in Phase 5.

For cross-host comparison, see artifact/aggregate.py.
"""

from typing import Any, Dict, List, Optional


_SEP  = "═" * 66
_LINE = "─" * 66


def print_summary(artifact: Dict[str, Any]) -> None:
    """Print a formatted summary of the artifact to stdout."""
    host = artifact.get("host", {})

    print(f"\n{_SEP}")
    print("   SYSTEM TRANSFER SPEED CHECK — ARTIFACT SUMMARY")
    print(_SEP)
    print(f"  Host     : {host.get('hostname', 'Unknown')}")
    print(f"  OS       : {host.get('os', 'Unknown')}  {host.get('os_version', '')[:50]}")
    print(f"  Python   : {host.get('python_version', 'Unknown')}")
    print(f"  Generated: {artifact.get('generated_on', 'Unknown')}")

    # ── Layer 1: Hardware ─────────────────────────────────────────────────────
    hw = artifact.get("hardware_baseline", {})
    if hw:
        print(f"\n{_LINE}")
        print("  Layer 1 — Hardware Baseline")
        print(_LINE)

        disk = hw.get("disk", {})
        if disk:
            print(f"  Disk type  : {disk.get('device_type') or 'Unknown'} / "
                  f"{disk.get('interface') or 'Unknown'}")
            print(f"  Seq Read   : {_fmt_mbps(disk.get('sequential_read_MBps'))}")
            print(f"  Seq Write  : {_fmt_mbps(disk.get('sequential_write_MBps'))}")
            print(f"  Test size  : {disk.get('test_file_size_MB', '?')} MB  "
                  f"(method: {disk.get('test_method', '?')})")

        cpu = hw.get("cpu", {})
        if cpu:
            print(f"  CPU        : {cpu.get('model') or 'Unknown'}")
            phys = cpu.get("cores_physical")
            logi = cpu.get("cores_logical")
            print(f"  Cores      : {phys} physical / {logi} logical")
            aes = cpu.get("aes_ni")
            aes_str = "✓ Yes (hardware crypto acceleration)" if aes is True \
                      else ("✗ No  (software AES — SSH/SCP will be CPU-bound)"
                            if aes is False else "Unknown")
            print(f"  AES-NI     : {aes_str}")

        ram = hw.get("ram", {})
        if ram:
            print(f"  RAM        : {ram.get('total_GB', 'Unknown')} GB")

        nic = hw.get("nic", {})
        ifaces = nic.get("interfaces", [])
        if ifaces:
            print(f"  NICs (up)  :")
            for i in ifaces:
                spd = i.get("negotiated_speed_Mbps")
                spd_str = f"{spd:,} Mbps" if spd else "speed unknown"
                fd  = i.get("full_duplex")
                fd_str = " full-duplex" if fd else (" half-duplex" if fd is False else "")
                name = i.get("name") or i.get("device") or "?"
                print(f"    · {name} : {spd_str}{fd_str}")
        fn = nic.get("filtered_note")
        if fn:
            print(f"  NIC filter : {fn}")

        # Power plan: only show if present (Windows only)
        pp = hw.get("power_plan")
        if pp:
            warn = pp.get("throttle_warning")
            if warn:
                print(f"\n  ⚠  {warn}")
            elif pp.get("is_high_performance"):
                print("  ✓  Power plan: High Performance")

    # ── Layer 2: Network ─────────────────────────────────────────────────────
    net = artifact.get("network_topology", {})
    if net:
        print(f"\n{_LINE}")
        print("  Layer 2 — Network Characterization")
        print(_LINE)

        gw = net.get("default_gateway")
        if gw:
            print(f"  Gateway    : {gw}")

        ping = net.get("ping", {})
        if ping and ping.get("avg_ms") is not None:
            loss = ping.get("packet_loss_pct", 0)
            loss_str = f"  ⚠ {loss}% loss!" if loss else "  no loss"
            print(f"  Ping       : avg {ping['avg_ms']} ms  "
                  f"jitter {ping.get('jitter_ms', '?')} ms{loss_str}"
                  f"  → {ping.get('target', '?')}")

        mtu = net.get("mtu", {})
        if mtu:
            eff = mtu.get("effective_mtu")
            cfg = mtu.get("interface_mtu")
            mtu_str = f"{eff}" if eff else "?"
            cfg_str = f"  (configured: {cfg})" if cfg else ""
            warn_flag = "  ⚠" if mtu.get("warning") else ""
            print(f"  MTU        : effective {mtu_str}{cfg_str}{warn_flag}")

        wifi = net.get("wifi", {})
        if wifi.get("connected"):
            ssid   = wifi.get("ssid", "?")
            band   = wifi.get("band", "?")
            rssi   = wifi.get("rssi_dBm")
            txrate = wifi.get("tx_rate_Mbps")
            rssi_str   = f"  {rssi} dBm" if rssi else ""
            txrate_str = f"  tx {txrate} Mbps" if txrate else ""
            print(f"  Wi-Fi      : {ssid}  {band}{rssi_str}{txrate_str}")
        elif wifi.get("connected") is False:
            print(f"  Wi-Fi      : not connected")

        dns = net.get("dns", {})
        avg_dns = dns.get("avg_resolve_ms")
        if avg_dns is not None:
            flag = "  ⚠ slow DNS!" if avg_dns > 100 else ""
            print(f"  DNS        : avg {avg_dns} ms  max {dns.get('max_resolve_ms')} ms{flag}")

        tcp = net.get("tcp_window", {})
        if tcp:
            at = tcp.get("auto_tuning")
            at_str = "enabled ✓" if at else ("DISABLED ⚠" if at is False else "?")
            recv   = tcp.get("recv_window_bytes")
            recv_str = f"  recv window {recv//1024} KB" if recv else ""
            print(f"  TCP        : auto-tuning {at_str}{recv_str}")

        bdp = net.get("bandwidth_delay_product")
        if bdp and bdp.get("bdp_KB"):
            print(f"  BDP        : {bdp['bdp_KB']} KB  "
                  f"(ideal window for {bdp['nic_speed_Mbps']} Mbps / {bdp['rtt_ms']} ms RTT)")

        hops = (net.get("traceroute") or {}).get("hops", [])
        if hops:
            print(f"  Traceroute : {len(hops)} hops to {net.get('traceroute', {}).get('target', '?')}")
            for h in hops[:5]:
                ms = f"{h['avg_ms']} ms" if h.get("avg_ms") else "*"
                print(f"    hop {h['hop']:2}  {h.get('ip', '*'):<18} {ms}")

    # ── Layer 4: Tuning Sweeps ────────────────────────────────────────────────
    tuning = artifact.get("tuning_results", [])
    if tuning:
        print(f"\n{_LINE}")
        print("  Layer 4 — Tuning Sweeps")
        print(_LINE)
        # Group by sweep, show best result for each
        sweeps: dict = {}
        for r in tuning:
            s = r.get("sweep", "?")
            if s not in sweeps:
                sweeps[s] = []
            sweeps[s].append(r)

        sweep_labels = {
            "block_size":   "Block Size",
            "thread_count": "Thread Count",
            "compression":  "Compression",
            "sync_mode":    "Sync Mode",
            "file_profile": "File Profile",
        }
        for sweep_key, sweep_results in sweeps.items():
            label = sweep_labels.get(sweep_key, sweep_key)
            ok    = [r for r in sweep_results if r.get("throughput_MBps") and not r.get("error")]
            if not ok:
                print(f"  {label:<18} : (no results)")
                continue
            best  = max(ok, key=lambda r: r["throughput_MBps"])
            worst = min(ok, key=lambda r: r["throughput_MBps"])
            print(f"  {label:<18} : best  {best['value']} @ {best['throughput_MBps']:>8.1f} MB/s")
            print(f"  {'':<18}   worst {worst['value']} @ {worst['throughput_MBps']:>8.1f} MB/s")

    # ── Layer 3: Protocol Benchmarks ──────────────────────────────────────────
    proto_results = artifact.get("protocol_results", [])
    if proto_results:
        print(f"\n{_LINE}")
        print("  Layer 3 — Protocol Benchmarks")
        print(_LINE)
        # Show the most recent run of each protocol
        seen: dict = {}
        for r in proto_results:
            seen[r.get("protocol")] = r   # last write wins → most recent
        print(f"  {'Protocol':<18} {'Direction':<9} {'Target':<18} {'MB/s':>8}  Notes/Error")
        print(f"  {'-'*18} {'-'*9} {'-'*18} {'-'*8}  {'-'*24}")
        for proto, r in seen.items():
            direction = r.get("direction", "?")
            tgt       = r.get("target") or "—"
            mbps      = r.get("throughput_MBps")
            mbps_str  = f"{mbps:>8.1f}" if mbps else f"{'—':>8}"
            err       = r.get("error")
            note      = (err or r.get("notes") or "")[:50]
            flag      = "  ⚠ ERROR" if err else ""
            print(f"  {proto:<18} {direction:<9} {tgt:<18} {mbps_str}  {note}{flag}")

    # ── Bottleneck Hints ──────────────────────────────────────────────────────
    hints = artifact.get("bottleneck_hints", [])
    if hints:
        print(f"\n{_LINE}")
        print("  Bottleneck Hints")
        print(_LINE)
        for h in hints:
            conf  = (h.get("confidence") or "?").upper()
            layer = h.get("layer") or "?"
            obs   = h.get("observation") or ""
            print(f"  [{conf}] {layer}: {obs}")
            action = h.get("suggested_action")
            if action:
                print(f"    → {action}")

    # ── Run History ───────────────────────────────────────────────────────────
    history = artifact.get("run_history", [])
    if history:
        print(f"\n{_LINE}")
        print(f"  Run History  ({len(history)} total run(s))")
        print(_LINE)
        for r in history[-5:]:
            print(f"  {r.get('timestamp', '')[:19]}Z  |  "
                  f"{r.get('os', '?'):8}  |  {r.get('summary', '')}")

    print(f"{_SEP}\n")


def _fmt_mbps(val: Any) -> str:
    if val is None:
        return "Not measured"
    return f"{val:>8,.1f} MB/s"
