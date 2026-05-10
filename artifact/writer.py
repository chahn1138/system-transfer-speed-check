"""
artifact/writer.py
==================
Load, update, and persist the transfer-profile.json artifact.

Design rules
------------
- load_artifact()  : returns existing artifact OR a fresh schema-valid one.
- save_artifact()  : atomic write; creates directories as needed.
- append_run()     : stamps a run_history entry; never overwrites history.
- add_bottleneck_hint() : appends a derived bottleneck observation.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from .schema import new_artifact, _now


def load_artifact(path: str) -> Dict[str, Any]:
    """
    Load artifact from disk.
    Returns a fresh artifact if the file does not exist or is corrupt.
    """
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[!] Could not read artifact at '{path}' ({e}); starting fresh.")
    return new_artifact()


def save_artifact(artifact: Dict[str, Any], path: str) -> None:
    """
    Write artifact to disk as formatted JSON.
    Creates parent directories if they do not exist.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, default=str)
    # Atomic replace
    if os.path.exists(path):
        os.replace(tmp_path, path)
    else:
        os.rename(tmp_path, path)


def append_run(
    artifact: Dict[str, Any],
    layers: List[str],
    os_name: str,
    notes: str = "",
) -> None:
    """Append a timestamped entry to run_history."""
    artifact.setdefault("run_history", []).append({
        "timestamp":  _now(),
        "os":         os_name,
        "layers_run": layers,
        "summary":    f"Ran {', '.join(layers)} probe(s) on {os_name}" + (f" — {notes}" if notes else ""),
    })


def add_bottleneck_hint(
    artifact: Dict[str, Any],
    layer: str,
    observation: str,
    confidence: str = "medium",
    suggested_action: str = "",
) -> None:
    """
    Append a derived bottleneck hint to the artifact.

    confidence : "high" | "medium" | "low"
    """
    artifact.setdefault("bottleneck_hints", []).append({
        "timestamp":        _now(),
        "layer":            layer,
        "observation":      observation,
        "confidence":       confidence,
        "suggested_action": suggested_action,
    })
