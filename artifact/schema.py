"""
artifact/schema.py
==================
Default JSON artifact schema.

new_artifact() returns a fresh, fully-structured dict that every
other artifact module works against. Fields start as None and are
populated as probes run.
"""

import platform
import socket
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"


def new_artifact() -> dict:
    """Return a fresh, empty artifact conforming to the current schema."""
    return {
        "schema_version":    SCHEMA_VERSION,
        "generated_on":      _now(),
        "host": {
            "hostname":       socket.gethostname(),
            "os":             platform.system(),
            "os_version":     platform.version(),
            "python_version": platform.python_version(),
        },
        "hardware_baseline": {},
        "network_topology":  {},
        "protocol_results":  [],
        "tuning_results":    [],
        "live_results":      [],
        "bottleneck_hints":  [],
        "run_history":       [],
    }


def _now() -> str:
    """Return current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
