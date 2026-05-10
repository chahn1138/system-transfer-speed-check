"""
report/summarize.py
===================
Human-readable console summary of the transfer-profile artifact.

Intentionally uses only stdlib so it works before 'pip install' is run.
A rich-based version will be added in Phase 5.
"""

from typing import Any, Dict


_SEP  = "═" * 62
_LINE = "─" * 62


def print_summary(artifact: Dict[str, Any]) -> None:
    """Print a formatted summary of the artifact to stdout."""
    host = artifact.get("host", {})

    print(f"\n{_SEP}")
    print("   SYSTEM TRANSFER SPEED CHECK — ARTIFACT SUMMARY")
    print(_SEP)
    print(f"  Host     : {host.get('hostname', 'Unknown')}")
    print(f"  OS       : {host.get('os', 'Unknown')}  {host.get('os_version', '')}")
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
                print(f"    · {i.get('name', '?')} : {spd_str}{fd_str}")

        pp = hw.get("power_plan", {})
        if pp:
            warn = pp.get("throttle_warning")
            if warn:
                print(f"\n  ⚠  {warn}")
            elif pp.get("is_high_performance"):
                print("  ✓  Power plan: High Performance")

    # ── Bottleneck Hints ──────────────────────────────────────────────────────
    hints = artifact.get("bottleneck_hints", [])
    if hints:
        print(f"\n{_LINE}")
        print("  Bottleneck Hints")
        print(_LINE)
        for h in hints[-5:]:  # show most recent 5
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
