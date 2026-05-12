#!/usr/bin/env python3
"""
tools/clean_artifact.py
=======================
Maintenance utility — removes stale sentinel error records from all host
artifacts in artifact/hosts/.

Sentinel records are written when a probe dependency (e.g. psutil) is
missing at run time. Once the dependency is installed and a real run
completes, these placeholders serve no purpose and pollute --compare output.

Sentinels removed
-----------------
  live_results     : scenario == "psutil-missing"
  protocol_results : protocol == "payload-gen" with error set
                     (payload generation failures from early aborted runs)

Usage
-----
  python tools/clean_artifact.py            # preview (dry-run)
  python tools/clean_artifact.py --apply    # write changes to disk
"""

import argparse
import json
import os
import sys

HOSTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "artifact", "hosts")


def _is_sentinel_live(record: dict) -> bool:
    return record.get("scenario") == "psutil-missing"


def _is_sentinel_protocol(record: dict) -> bool:
    return record.get("protocol") == "payload-gen" and bool(record.get("error"))


def clean_artifact(artifact: dict) -> tuple[dict, list[str]]:
    """
    Remove sentinel records from *artifact* in-place.
    Returns (modified_artifact, list_of_change_descriptions).
    """
    changes = []

    live = artifact.get("live_results", [])
    clean_live = [r for r in live if not _is_sentinel_live(r)]
    removed_live = len(live) - len(clean_live)
    if removed_live:
        artifact["live_results"] = clean_live
        changes.append(f"  removed {removed_live} psutil-missing sentinel(s) from live_results")

    proto = artifact.get("protocol_results", [])
    clean_proto = [r for r in proto if not _is_sentinel_protocol(r)]
    removed_proto = len(proto) - len(clean_proto)
    if removed_proto:
        artifact["protocol_results"] = clean_proto
        changes.append(f"  removed {removed_proto} payload-gen error sentinel(s) from protocol_results")

    return artifact, changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove stale sentinel error records from host artifacts.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to disk (default: dry-run / preview only)",
    )
    parser.add_argument(
        "--hosts-dir",
        default=HOSTS_DIR,
        metavar="DIR",
        help=f"Path to host artifact directory (default: {HOSTS_DIR})",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.hosts_dir):
        print(f"[!] Hosts directory not found: {args.hosts_dir}")
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[clean_artifact] mode={mode}  dir={args.hosts_dir}\n")

    any_changes = False
    for fname in sorted(os.listdir(args.hosts_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(args.hosts_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                artifact = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  [!] {fname}: could not read — {exc}")
            continue

        artifact, changes = clean_artifact(artifact)

        if not changes:
            print(f"  {fname}: clean — nothing to remove")
            continue

        any_changes = True
        print(f"  {fname}:")
        for c in changes:
            print(c)

        if args.apply:
            tmp = fpath + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(artifact, f, indent=2)
                f.write("\n")
            os.replace(tmp, fpath)
            print(f"    → written")

    if not args.apply and any_changes:
        print("\n  (dry-run — re-run with --apply to write changes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
