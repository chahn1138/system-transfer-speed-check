#!/usr/bin/env python3
"""
run_probes.py
=============
System Transfer Speed Check — main entry point.

Detects the host OS, runs the requested probe layers, updates the
intelligence artifact, and prints a human-readable summary.

Usage
-----
  python run_probes.py                        # run all implemented layers
  python run_probes.py --layers hardware      # Layer 1 only
  python run_probes.py --summary              # print artifact, no probes
  python run_probes.py --reset                # clear artifact, then run
  python run_probes.py --output /path/to/x.json
"""

import argparse
import os
import sys

# Ensure project root is importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from probe.platform_utils import detect_os, hostname, os_version, python_version
from probe.hardware import probe_hardware
from artifact.writer import load_artifact, save_artifact, append_run, add_bottleneck_hint
from report.summarize import print_summary

# ── Constants ─────────────────────────────────────────────────────────────────

SUPPORTED_LAYERS  = ["hardware", "network", "protocols", "tuning", "live"]
DEFAULT_ARTIFACT  = os.path.join("artifact", "transfer-profile.json")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run_probes.py",
        description="System Transfer Speed Check — cross-platform transfer performance toolkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Implemented layers (Phase 1): hardware\n"
            "Coming in Phase 2+: network, protocols, tuning, live"
        ),
    )
    p.add_argument(
        "--layers",
        default="all",
        metavar="LAYERS",
        help=(
            f"Comma-separated probe layers to run. "
            f"Options: {', '.join(SUPPORTED_LAYERS)}, all. "
            "Default: all (currently runs: hardware)"
        ),
    )
    p.add_argument(
        "--target",
        default=None,
        metavar="user@host",
        help="Remote host for network/protocol tests (Phase 2+)",
    )
    p.add_argument(
        "--output",
        default=DEFAULT_ARTIFACT,
        metavar="PATH",
        help=f"Path to the JSON artifact file. Default: {DEFAULT_ARTIFACT}",
    )
    p.add_argument(
        "--summary",
        action="store_true",
        help="Print a summary of the current artifact and exit (no probes run)",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing artifact and start fresh before running probes",
    )
    return p.parse_args()


def resolve_layers(layers_arg: str) -> list:
    """Normalise the --layers argument to a list of layer names."""
    if layers_arg.strip().lower() == "all":
        # Phase 1: only hardware is implemented; expand as phases ship
        return ["hardware"]
    return [l.strip().lower() for l in layers_arg.split(",") if l.strip()]


# ── Bottleneck analysis (Layer 1) ─────────────────────────────────────────────

def _analyse_hardware(artifact: dict) -> None:
    """
    Derive bottleneck hints from Layer 1 results and append them to the artifact.
    This grows as we add more baselines to compare against.
    """
    hw   = artifact.get("hardware_baseline", {})
    disk = hw.get("disk", {})
    cpu  = hw.get("cpu", {})
    pp   = hw.get("power_plan", {})

    # Warn if sequential write is suspiciously low (< 80 MB/s → likely HDD or USB)
    write = disk.get("sequential_write_MBps")
    if write is not None and write < 80:
        add_bottleneck_hint(
            artifact,
            layer="hardware.disk",
            observation=f"Sequential write speed is {write} MB/s — likely spinning HDD or USB storage.",
            confidence="high",
            suggested_action="Consider an NVMe or SATA SSD to remove disk as a transfer bottleneck.",
        )

    # Warn if AES-NI is absent
    if cpu.get("aes_ni") is False:
        add_bottleneck_hint(
            artifact,
            layer="hardware.cpu",
            observation="No AES-NI detected. Encrypted transfers (SSH/SCP/SFTP) will be CPU-bound.",
            confidence="high",
            suggested_action=(
                "Use netcat or rsync without SSH for internal trusted transfers "
                "to avoid the encryption bottleneck."
            ),
        )

    # Warn if power plan is not high performance (Windows)
    if pp.get("is_high_performance") is False:
        add_bottleneck_hint(
            artifact,
            layer="hardware.power_plan",
            observation=f"Non-high-performance power plan active: {pp.get('active_scheme', '?')}",
            confidence="medium",
            suggested_action=(
                "Run: powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c "
                "to enable High Performance before benchmarking."
            ),
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    args = parse_args()

    # ── Summary-only mode ─────────────────────────────────────────────────────
    if args.summary:
        artifact = load_artifact(args.output)
        print_summary(artifact)
        return 0

    # ── Optional reset ────────────────────────────────────────────────────────
    if args.reset and os.path.exists(args.output):
        os.remove(args.output)
        print(f"[+] Artifact reset: {args.output}")

    # ── Setup ─────────────────────────────────────────────────────────────────
    artifact = load_artifact(args.output)
    os_name  = detect_os()
    layers   = resolve_layers(args.layers)

    print(f"\n[+] System Transfer Speed Check")
    print(f"    Host   : {hostname()}")
    print(f"    OS     : {os_name}  {os_version()[:60]}")
    print(f"    Layers : {', '.join(layers)}")
    if args.target:
        print(f"    Target : {args.target}")

    # ── Run layers ────────────────────────────────────────────────────────────
    if "hardware" in layers:
        print("\n[Layer 1] Hardware Baseline — running...")
        artifact["hardware_baseline"] = probe_hardware()
        _analyse_hardware(artifact)
        print("[Layer 1] Complete.")

    if "network" in layers and "hardware" not in layers:
        print("\n[Layer 2] Network — not yet implemented (coming in Phase 2)")

    if "protocols" in layers:
        print("\n[Layer 3] Protocols — not yet implemented (coming in Phase 2)")

    if "tuning" in layers:
        print("\n[Layer 4] Tuning — not yet implemented (coming in Phase 3)")

    if "live" in layers:
        print("\n[Layer 5] Live telemetry — not yet implemented (coming in Phase 4)")

    # ── Persist ───────────────────────────────────────────────────────────────
    artifact["host"] = {
        "hostname":       hostname(),
        "os":             os_name,
        "os_version":     os_version(),
        "python_version": python_version(),
    }
    append_run(artifact, layers, os_name)
    save_artifact(artifact, args.output)
    print(f"\n[+] Artifact saved → {args.output}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print_summary(artifact)

    return 0


if __name__ == "__main__":
    sys.exit(main())
