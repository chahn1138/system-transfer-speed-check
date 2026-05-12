#!/usr/bin/env python3
"""
run_probes.py
=============
System Transfer Speed Check — main entry point.

Each host writes its artifact to artifact/hosts/<hostname>.json so that
multiple machines can share a single git repo and their results accumulate
side-by-side. Use --compare to view a cross-host summary.

Usage
-----
  python run_probes.py                         # all implemented layers
  python run_probes.py --layers hardware       # Layer 1 only
  python run_probes.py --layers network        # Layer 2 only
  python run_probes.py --layers hardware,network
  python run_probes.py --summary               # print this host's artifact
  python run_probes.py --compare               # compare all hosts in the store
  python run_probes.py --reset                 # clear this host's artifact
  python run_probes.py --target 192.168.1.1    # network probes against a target
  python run_probes.py --output /custom/path.json
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe.platform_utils import detect_os, hostname, os_version, python_version
from probe.hardware        import probe_hardware
from probe.network         import probe_network
from probe.protocols       import probe_protocols
from probe.tuning          import probe_tuning
from probe.live            import probe_live
from artifact.writer       import load_artifact, save_artifact, append_run, add_bottleneck_hint
from artifact.aggregate    import build_aggregate
from report.rich_compare   import print_comparison
from report.rich_summary   import print_summary

# ── Constants ─────────────────────────────────────────────────────────────────

HOSTS_DIR        = os.path.join("artifact", "hosts")
SUPPORTED_LAYERS = ["hardware", "network", "protocols", "tuning", "live"]


def default_artifact_path() -> str:
    """Per-host artifact path: artifact/hosts/<hostname>.json"""
    safe_name = hostname().replace(" ", "_").lower()
    return os.path.join(HOSTS_DIR, f"{safe_name}.json")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_probes.py",
        description="System Transfer Speed Check — cross-platform transfer performance toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Implemented layers:\n"
            "  Phase 1: hardware\n"
            "  Phase 2: network\n"
            "Coming: protocols (Phase 3), tuning (Phase 4), live (Phase 5)"
        ),
    )
    p.add_argument(
        "--layers",
        default="all",
        metavar="LAYERS",
        help=(
            f"Comma-separated probe layers to run. "
            f"Options: {', '.join(SUPPORTED_LAYERS)}, all.  Default: all"
        ),
    )
    p.add_argument(
        "--target",
        default=None,
        metavar="HOST",
        help="Remote host IP/hostname for network/protocol probes",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help=f"Override artifact path (default: {HOSTS_DIR}/<hostname>.json)",
    )
    p.add_argument(
        "--summary",
        action="store_true",
        help="Print this host's artifact summary and exit",
    )
    p.add_argument(
        "--compare",
        action="store_true",
        help="Print a cross-host comparison of all artifacts in the hosts store and exit",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Delete this host's artifact before running (start fresh)",
    )
    p.add_argument(
        "--payload-mb",
        type=int,
        default=256,
        metavar="MB",
        help="Size of the test payload in MB for protocol benchmarks (default: 256)",
    )
    return p.parse_args()


def resolve_layers(layers_arg: str) -> list:
    if layers_arg.strip().lower() == "all":
        return ["hardware", "network", "protocols", "tuning", "live"]   # implemented phases
    return [l.strip().lower() for l in layers_arg.split(",") if l.strip()]


# ── Bottleneck analysis ───────────────────────────────────────────────────────

def _analyse_hardware(artifact: dict) -> None:
    hw   = artifact.get("hardware_baseline", {})
    disk = hw.get("disk", {})
    cpu  = hw.get("cpu", {})
    pp   = hw.get("power_plan", {})

    write = disk.get("sequential_write_MBps")
    if write is not None and write < 80:
        add_bottleneck_hint(
            artifact, layer="hardware.disk",
            observation=f"Sequential write speed is {write} MB/s — likely spinning HDD or USB storage.",
            confidence="high",
            suggested_action="Consider an NVMe or SATA SSD to remove disk as a transfer bottleneck.",
        )

    if cpu.get("aes_ni") is False:
        add_bottleneck_hint(
            artifact, layer="hardware.cpu",
            observation="No AES-NI detected. Encrypted transfers (SSH/SCP/SFTP) will be CPU-bound.",
            confidence="high",
            suggested_action=(
                "Use netcat or rsync without SSH for internal trusted transfers "
                "to avoid the encryption bottleneck."
            ),
        )

    # Power plan: Windows only — only flag if we actually got a result
    if pp and pp.get("is_high_performance") is False:
        add_bottleneck_hint(
            artifact, layer="hardware.power_plan",
            observation=f"Non-high-performance power plan active: {pp.get('active_scheme', '?')}",
            confidence="medium",
            suggested_action=(
                "Run: powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c "
                "to enable High Performance before benchmarking."
            ),
        )


def _analyse_network(artifact: dict) -> None:
    net = artifact.get("network_topology", {})

    ping = net.get("ping", {})
    loss = ping.get("packet_loss_pct")
    if loss is not None and loss > 0:
        add_bottleneck_hint(
            artifact, layer="network.ping",
            observation=f"Packet loss detected: {loss}% to {ping.get('target', '?')}",
            confidence="high",
            suggested_action="Investigate cable/Wi-Fi quality; even 0.1% loss collapses TCP throughput on long paths.",
        )

    jitter = ping.get("jitter_ms")
    if jitter is not None and jitter > 5:
        add_bottleneck_hint(
            artifact, layer="network.ping",
            observation=f"High jitter detected: {jitter} ms. TCP window efficiency will suffer.",
            confidence="medium",
            suggested_action="Check for Wi-Fi interference or a congested switch port.",
        )

    mtu = net.get("mtu", {})
    if mtu and mtu.get("warning"):
        add_bottleneck_hint(
            artifact, layer="network.mtu",
            observation=mtu["warning"],
            confidence="high",
            suggested_action="Align MTU across all devices in the path or reduce transfer MTU to avoid fragmentation.",
        )

    tcp = net.get("tcp_window", {})
    if tcp.get("notes"):
        add_bottleneck_hint(
            artifact, layer="network.tcp_window",
            observation=tcp["notes"],
            confidence="high",
            suggested_action="Enable auto-tuning (see notes field for command).",
        )

    wifi = net.get("wifi", {})
    if wifi.get("connected") and wifi.get("band") == "2.4 GHz":
        add_bottleneck_hint(
            artifact, layer="network.wifi",
            observation="Connected to 2.4 GHz Wi-Fi. Maximum practical throughput ~70 Mbps.",
            confidence="medium",
            suggested_action="Switch to 5 GHz or 6 GHz band, or use wired Ethernet for transfers.",
        )

    bdp = net.get("bandwidth_delay_product")
    if bdp and bdp.get("bdp_bytes"):
        recv = (tcp.get("recv_window_bytes") or 0)
        if recv and recv < bdp["bdp_bytes"]:
            add_bottleneck_hint(
                artifact, layer="network.tcp_window",
                observation=(
                    f"TCP receive window ({recv//1024} KB) is smaller than the "
                    f"Bandwidth-Delay Product ({bdp['bdp_KB']} KB). "
                    "Throughput on this path is window-limited."
                ),
                confidence="high",
                suggested_action=(
                    "Increase TCP receive buffer size to at least "
                    f"{int(bdp['bdp_bytes'] * 1.5 / 1024)} KB."
                ),
            )


def _analyse_protocols(artifact: dict) -> None:
    results = artifact.get("protocol_results", [])
    if not results:
        return

    # Find the fastest and slowest successful local benchmarks
    local_ok = [r for r in results if r.get("direction") == "local" and r.get("throughput_MBps")]
    net_ok   = [r for r in results if r.get("direction") == "send"  and r.get("throughput_MBps")]

    if local_ok:
        # rsync has inherent checksum overhead (~200-300 MB/s is normal); exclude it
        # from the generic "slow" threshold check to avoid false positives.
        non_rsync_local = [r for r in local_ok if r.get("protocol") != "rsync"]
        slowest = min(non_rsync_local, key=lambda r: r["throughput_MBps"]) if non_rsync_local else None
        fastest = max(local_ok, key=lambda r: r["throughput_MBps"])
        if slowest and slowest["throughput_MBps"] < 500:
            add_bottleneck_hint(
                artifact, layer="protocols.local",
                observation=(
                    f"{slowest['protocol']} local copy: {slowest['throughput_MBps']:.0f} MB/s "
                    f"— slower than expected for modern SSD storage."
                ),
                confidence="medium",
                suggested_action=(
                    "Check disk health and available space; run Layer 1 disk probes to compare "
                    "sequential throughput vs protocol overhead."
                ),
            )

    if net_ok:
        for r in net_ok:
            hw   = artifact.get("hardware_baseline", {})
            nics = hw.get("nic", {}).get("interfaces", [])
            link_mbps = max((n.get("negotiated_speed_Mbps") or 0 for n in nics), default=0)
            if link_mbps and r["throughput_Mbps"] is not None:
                efficiency = r["throughput_Mbps"] / link_mbps * 100
                if efficiency < 40:
                    add_bottleneck_hint(
                        artifact, layer=f"protocols.{r['protocol']}",
                        observation=(
                            f"{r['protocol']} to {r['target']}: {r['throughput_MBps']:.1f} MB/s "
                            f"({efficiency:.0f}% of {link_mbps} Mbps link). "
                            "Significant protocol or encryption overhead."
                        ),
                        confidence="medium",
                        suggested_action=(
                            "Compare scp vs rsync-ssh vs robocopy-unc to isolate whether "
                            "the bottleneck is encryption CPU cost, TCP tuning, or SMB overhead."
                        ),
                    )

    # Flag any benchmark errors
    errors = [r for r in results if r.get("error")]
    for r in errors:
        add_bottleneck_hint(
            artifact, layer=f"protocols.{r['protocol']}",
            observation=f"{r['protocol']} benchmark failed: {r['error'][:120]}",
            confidence="high",
            suggested_action="Ensure the tool is installed and (for network tests) SSH key auth is configured.",
        )


def _analyse_tuning(artifact: dict) -> None:
    results = artifact.get("tuning_results", [])
    if not results:
        return

    # ── Block size: find the sweet spot ──────────────────────────────────────
    bs_ok = [r for r in results if r.get("sweep") == "block_size"
             and r.get("throughput_MBps") and not r.get("error")]
    if bs_ok:
        best_bs  = max(bs_ok, key=lambda r: r["throughput_MBps"])
        worst_bs = min(bs_ok, key=lambda r: r["throughput_MBps"])
        ratio    = best_bs["throughput_MBps"] / max(worst_bs["throughput_MBps"], 0.01)
        if ratio > 2:
            add_bottleneck_hint(
                artifact, layer="tuning.block_size",
                observation=(
                    f"Block size has a {ratio:.1f}x throughput impact: "
                    f"{worst_bs['value']} KB = {worst_bs['throughput_MBps']:.0f} MB/s, "
                    f"{best_bs['value']} KB = {best_bs['throughput_MBps']:.0f} MB/s."
                ),
                confidence="high",
                suggested_action=(
                    f"Configure transfer tools to use ~{best_bs['value']} KB blocks. "
                    "robocopy /256, rsync --block-size, rclone --s3-chunk-size."
                ),
            )

    # ── Thread count: flag if more threads helped significantly ───────────────
    tc_ok   = [r for r in results if r.get("sweep") == "thread_count"
               and r.get("throughput_MBps") and not r.get("error")]
    if tc_ok:
        single  = next((r for r in tc_ok if r["value"] == 1), None)
        best_tc = max(tc_ok, key=lambda r: r["throughput_MBps"])
        if single and best_tc["value"] != 1:
            gain = best_tc["throughput_MBps"] / max(single["throughput_MBps"], 0.01)
            if gain > 1.3:
                add_bottleneck_hint(
                    artifact, layer="tuning.thread_count",
                    observation=(
                        f"{best_tc['value']} parallel streams are {gain:.1f}x faster "
                        f"than single-stream ({best_tc['throughput_MBps']:.0f} vs "
                        f"{single['throughput_MBps']:.0f} MB/s)."
                    ),
                    confidence="high",
                    suggested_action=(
                        f"Use {best_tc['value']} threads in your transfer tool. "
                        f"robocopy /MT:{best_tc['value']}, rclone --transfers={best_tc['value']}."
                    ),
                )

    # ── Sync mode: flag high fsync cost ──────────────────────────────────────
    sm_ok = {r["value"]: r for r in results if r.get("sweep") == "sync_mode"
             and r.get("throughput_MBps") and not r.get("error")}
    if "buffered" in sm_ok and "fsync_each" in sm_ok:
        ratio = sm_ok["buffered"]["throughput_MBps"] / max(sm_ok["fsync_each"]["throughput_MBps"], 0.01)
        if ratio > 5:
            add_bottleneck_hint(
                artifact, layer="tuning.sync_mode",
                observation=(
                    f"Per-write fsync is {ratio:.0f}x slower than buffered I/O "
                    f"({sm_ok['fsync_each']['throughput_MBps']:.0f} vs "
                    f"{sm_ok['buffered']['throughput_MBps']:.0f} MB/s). "
                    "Storage commit latency is high."
                ),
                confidence="medium",
                suggested_action=(
                    "Prefer buffered transfer tools. Avoid /J (unbuffered) robocopy, "
                    "O_SYNC/O_DSYNC open flags, or tools that fsync after every chunk."
                ),
            )

    # ── File profile: small-file penalty ─────────────────────────────────────
    fp_ok = [r for r in results if r.get("sweep") == "file_profile"
             and r.get("throughput_MBps") and not r.get("error")]
    if fp_ok:
        large_f = max(fp_ok, key=lambda r: r["throughput_MBps"])
        small_f = min(fp_ok, key=lambda r: r["throughput_MBps"])
        ratio   = large_f["throughput_MBps"] / max(small_f["throughput_MBps"], 0.01)
        if ratio > 3:
            add_bottleneck_hint(
                artifact, layer="tuning.file_profile",
                observation=(
                    f"Small-file penalty: {ratio:.1f}x throughput difference — "
                    f"{small_f['value']} = {small_f['throughput_MBps']:.0f} MB/s vs "
                    f"{large_f['value']} = {large_f['throughput_MBps']:.0f} MB/s."
                ),
                confidence="high",
                suggested_action=(
                    "Bundle small files into tar/zip before transferring, or use rclone "
                    "with --transfers and --checkers tuned up for small-file workloads."
                ),
            )


def _analyse_live(artifact: dict) -> None:
    results = artifact.get("live_results", [])
    ok = [r for r in results if not r.get("error") and r.get("telemetry")]
    if not ok:
        return

    for r in ok:
        tel      = r["telemetry"]
        mbps     = r.get("throughput_MBps")
        scenario = r.get("scenario", "?")

        # CPU saturation: peak > 85% during transfer
        cpu_peak = tel.get("cpu_pct_peak")
        if cpu_peak is not None and cpu_peak > 85:
            add_bottleneck_hint(
                artifact, layer="live.cpu",
                observation=(
                    f"CPU peaked at {cpu_peak}% during '{scenario}' transfer "
                    f"({mbps} MB/s). Transfer is CPU-bound."
                ),
                confidence="high",
                suggested_action=(
                    "Enable hardware offload if available. For encrypted transfers, "
                    "verify AES-NI is active. Consider reducing thread count or "
                    "switching to a less CPU-intensive protocol."
                ),
            )

        # Check if any single core is pinned (per-core peak > 95%)
        per_core = tel.get("cpu_pct_per_core_peak") or []
        pinned   = [i for i, v in enumerate(per_core) if v is not None and v > 95]
        if pinned:
            add_bottleneck_hint(
                artifact, layer="live.cpu",
                observation=(
                    f"Core(s) {pinned} pinned at >95% during '{scenario}'. "
                    "Single-threaded bottleneck in the transfer pipeline."
                ),
                confidence="high",
                suggested_action=(
                    "Increase parallelism — use multi-threaded robocopy /MT:n, "
                    "rclone --transfers, or rsync with parallel invocations."
                ),
            )

        # Disk write saturation: measured write peak vs Layer 1 sequential ceiling
        hw_write = (artifact.get("hardware_baseline", {})
                    .get("disk", {}).get("sequential_write_MBps"))
        dw_peak  = tel.get("disk_write_MBps_peak")
        if hw_write and dw_peak and dw_peak > hw_write * 0.90:
            add_bottleneck_hint(
                artifact, layer="live.disk",
                observation=(
                    f"Disk write during '{scenario}' hit {dw_peak} MB/s — "
                    f"{dw_peak/hw_write*100:.0f}% of sequential ceiling ({hw_write} MB/s). "
                    "Disk is the bottleneck."
                ),
                confidence="high",
                suggested_action=(
                    "Disk is saturated. Reducing thread count will not help. "
                    "Consider faster storage or splitting writes across multiple volumes."
                ),
            )

        # Memory pressure: available < 2 GB at any point
        mem_min = tel.get("mem_available_GB_min")
        if mem_min is not None and mem_min < 2.0:
            add_bottleneck_hint(
                artifact, layer="live.memory",
                observation=(
                    f"Available memory dropped to {mem_min} GB during '{scenario}'. "
                    "OS write buffer eviction may stall transfers."
                ),
                confidence="medium",
                suggested_action=(
                    "Close other applications before large transfers. "
                    "Consider increasing virtual memory / swap."
                ),
            )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    args    = parse_args()
    os_name = detect_os()
    host    = hostname()
    artifact_path = args.output or default_artifact_path()

    # ── Compare mode ─────────────────────────────────────────────────────────
    if args.compare:
        agg = build_aggregate(HOSTS_DIR)
        print_comparison(agg)
        return 0

    # ── Summary mode ─────────────────────────────────────────────────────────
    if args.summary:
        artifact = load_artifact(artifact_path)
        print_summary(artifact)
        return 0

    # ── Optional reset ────────────────────────────────────────────────────────
    if args.reset and os.path.exists(artifact_path):
        os.remove(artifact_path)
        print(f"[+] Artifact reset: {artifact_path}")

    # ── Setup ─────────────────────────────────────────────────────────────────
    artifact = load_artifact(artifact_path)
    layers   = resolve_layers(args.layers)

    print(f"\n[+] System Transfer Speed Check")
    print(f"    Host   : {host}")
    print(f"    OS     : {os_name}  {os_version()[:60]}")
    print(f"    Layers : {', '.join(layers)}")
    if args.target:
        print(f"    Target : {args.target}")
    if "protocols" in layers:
        print(f"    Payload: {args.payload_mb} MB")
    print(f"    Output : {artifact_path}")

    # Clear old hints so analysis is always fresh for this run
    artifact["bottleneck_hints"] = []

    # ── Layer 1: Hardware ─────────────────────────────────────────────────────
    if "hardware" in layers:
        print("\n[Layer 1] Hardware Baseline — running...")
        artifact["hardware_baseline"] = probe_hardware()
        _analyse_hardware(artifact)
        print("[Layer 1] Complete.")

    # ── Layer 2: Network ─────────────────────────────────────────────────────
    if "network" in layers:
        print("\n[Layer 2] Network Characterization — running...")
        artifact["network_topology"] = probe_network(target=args.target)
        _analyse_network(artifact)
        print("[Layer 2] Complete.")

    # ── Layer 3: Protocol Benchmarks ─────────────────────────────────────────
    if "protocols" in layers:
        print("\n[Layer 3] Protocol Benchmarks — running...")
        new_results = probe_protocols(target=args.target, payload_mb=args.payload_mb)
        # Append to existing protocol_results (accumulate across runs)
        existing = artifact.get("protocol_results", [])
        artifact["protocol_results"] = existing + new_results
        _analyse_protocols(artifact)
        print("[Layer 3] Complete.")

    # ── Layer 4: Tuning Sweeps ────────────────────────────────────────────────
    if "tuning" in layers:
        print("\n[Layer 4] Tuning Sweeps — running...")
        new_tuning = probe_tuning(payload_mb=args.payload_mb)
        existing_t = artifact.get("tuning_results", [])
        artifact["tuning_results"] = existing_t + new_tuning
        _analyse_tuning(artifact)
        print("[Layer 4] Complete.")

    # ── Layer 5: Live Telemetry ─────────────────────────────────────────────
    if "live" in layers:
        print("\n[Layer 5] Live Telemetry — running...")
        new_live   = probe_live(payload_mb=args.payload_mb)
        existing_l = artifact.get("live_results", [])
        artifact["live_results"] = existing_l + new_live
        _analyse_live(artifact)
        print("[Layer 5] Complete.")

    # ── Persist ───────────────────────────────────────────────────────────────
    artifact["host"] = {
        "hostname":       host,
        "os":             os_name,
        "os_version":     os_version(),
        "python_version": python_version(),
    }
    append_run(artifact, layers, os_name)
    save_artifact(artifact, artifact_path)
    print(f"\n[+] Artifact saved → {artifact_path}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print_summary(artifact)

    return 0


if __name__ == "__main__":
    sys.exit(main())
