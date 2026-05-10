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
from artifact.writer       import load_artifact, save_artifact, append_run, add_bottleneck_hint
from artifact.aggregate    import build_aggregate, print_comparison
from report.summarize      import print_summary

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
    return p.parse_args()


def resolve_layers(layers_arg: str) -> list:
    if layers_arg.strip().lower() == "all":
        return ["hardware", "network"]   # implemented phases
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

    # ── Stubs for future phases ───────────────────────────────────────────────
    for layer, phase in [("protocols", 3), ("tuning", 4), ("live", 5)]:
        if layer in layers:
            print(f"\n[Layer {phase}] {layer.title()} — not yet implemented (Phase {phase})")

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
