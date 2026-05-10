# Comprehensive Prompt — system-transfer-speed-check

> This document is the authoritative design brief for this project.
> It is intended to be handed to an AI coding assistant (or a human developer)
> to fully reconstruct intent, scope, and constraints without prior context.

---

## Project Statement

Build a cross-platform Python toolkit that measures, diagnoses, and learns
about **file transfer performance** across Windows, Linux, and macOS systems.

The toolkit must:

1. **Establish physical baselines** — probe every layer of the transfer stack
   (disk, CPU, RAM, NIC, PCIe) to determine the theoretical maximum throughput
   before any transfer is attempted. Never assume speed. Never skip baselines.

2. **Identify bottlenecks** — during and after transfers, determine which
   element in the chain is the constraining factor:
   - Transfer method (single-stream vs. multi-threaded vs. parallel chunked)
   - Transfer tooling (`scp`, `rsync`, `robocopy`, `rclone`, `netcat`, `curl`,
     `bbcp`, SMB, HTTP/S, etc.)
   - Hardware layout (which physical disk, which NIC, which PCIe slot)
   - Network path (LAN, Wi-Fi band, WAN, VPN, switch quality)
   - OS and software overhead (antivirus, power plan, encryption cost, etc.)

3. **Suggest and run additional probes** — the full probe surface includes but
   is not limited to:
   - MTU / jumbo frame discovery
   - TCP window scaling and Bandwidth-Delay Product analysis
   - Packet loss, jitter, and retransmit rates
   - DNS resolution overhead
   - NIC negotiated link speed vs. advertised speed
   - Wi-Fi band, RSSI, and channel congestion
   - CPU AES-NI hardware crypto acceleration presence
   - Windows Power Plan throttling impact
   - Antivirus real-time scan impact (opt-in, controlled)
   - Switch port error counters (where accessible)
   - SMB version negotiation
   - Compression benefit/cost by data type
   - Encryption cost with and without hardware offload

4. **Produce a living JSON intelligence artifact** — a structured file
   (`transfer-profile.json`) that:
   - Starts empty / naive on first run
   - Accumulates results across runs with full timestamps
   - Derives `bottleneck_hints` from the script's own analysis
   - Is designed to be **consumed by downstream workflows** as structured input
   - Lives outside the codebase — it is a data artifact, not source code

---

## Platform Requirements

This is a **cross-platform project**. The three supported OSes are treated as
equals, each with a **fully native mindset**:

| Platform | Native tools to leverage |
|----------|--------------------------|
| **Windows** | `robocopy`, `netsh`, WMI/CIM via `subprocess`, `Get-NetAdapter` (PowerShell), `diskspd` |
| **Linux** | `fio`, `hdparm`, `dd`, `iperf3`, `ethtool`, `ss`, `ip route`, `tc`, `rsync`, `scp` |
| **macOS** | `diskutil`, `system_profiler`, `networksetup`, `iperf3`, `rsync`, `nettop`, `airport` |

Python is the **unifying runtime**. Use `platform.system()` to dispatch to
OS-specific implementations. Do not homogenize where native tools are clearly
superior — wrap them.

Any given run executes entirely on one OS. The project does not require
simultaneous multi-OS orchestration (though remote endpoints may be of any OS).

---

## Language & Dependencies

- **Primary language**: Python 3.10+
- **Subprocess calls**: native OS commands, called via `subprocess.run()` with
  output capture
- **JSON**: stdlib `json` module for artifact I/O
- **Optional / phase-gated**: `paramiko` or `fabric` for SSH transfers,
  `psutil` for live system telemetry, `rich` for console output

---

## Artifact Schema (target shape)

```json
{
  "schema_version": "1.0",
  "generated_on": "<ISO 8601 timestamp>",
  "host": {
    "hostname": "",
    "os": "",
    "os_version": "",
    "python_version": ""
  },
  "hardware_baseline": {
    "disk": {
      "sequential_read_MBps": null,
      "sequential_write_MBps": null,
      "random_read_IOPS": null,
      "random_write_IOPS": null,
      "device_type": null,
      "interface": null
    },
    "cpu": {
      "cores_physical": null,
      "cores_logical": null,
      "aes_ni": null,
      "model": null
    },
    "ram": {
      "total_GB": null,
      "bandwidth_GBps": null
    },
    "nic": {
      "name": null,
      "negotiated_speed_Mbps": null,
      "duplex": null,
      "driver": null
    }
  },
  "network_topology": {
    "interfaces": [],
    "default_route": null,
    "dns_servers": [],
    "wifi": {
      "band": null,
      "rssi_dBm": null,
      "channel": null,
      "standard": null
    }
  },
  "protocol_results": [
    {
      "tool": "",
      "route": "",
      "timestamp": "",
      "file_size_MB": null,
      "duration_s": null,
      "throughput_MBps": null,
      "threads": null,
      "compression": null,
      "encryption": null,
      "notes": ""
    }
  ],
  "bottleneck_hints": [
    {
      "timestamp": "",
      "layer": "",
      "observation": "",
      "confidence": "",
      "suggested_action": ""
    }
  ],
  "run_history": [
    {
      "timestamp": "",
      "os": "",
      "layers_run": [],
      "summary": ""
    }
  ]
}
```

---

## Probe Overhead Policy

Some probes are expensive or require controlled conditions. The following
opt-in flags gate them:

| Flag | Probe | Why opt-in |
|------|-------|------------|
| `--probe-antivirus` | AV impact test (transfer with/without exclusion) | Requires AV config access |
| `--probe-iperf` | `iperf3` TCP/UDP throughput | Requires iperf3 server on remote end |
| `--probe-fio` | Full `fio` disk sweep | Extended runtime (minutes) |
| `--probe-destructive` | Write tests that consume real disk space | Disk space cost |

All other probes run by default. Runtime for a default run should be under
2 minutes on any supported platform.

---

## Entry Point Behavior

```
python run_probes.py [OPTIONS]

Options:
  --layers      Comma-separated list of layers to run
                (hardware, network, protocols, tuning, live | default: all)
  --target      Remote host for protocol/network tests (user@host)
  --output      Path to artifact JSON (default: artifact/transfer-profile.json)
  --probe-*     Opt-in flags for expensive probes (see above)
  --summary     Print human-readable summary of current artifact and exit
  --reset       Clear the artifact and start fresh
```

---

## Guiding Principles

- **Never assume speed.** Every run begins with discovery, not expectation.
- **The code stays clean; the learning lives in the artifact.**
- **Native is better than generic** — each OS gets its best tools.
- **Overhead must be earned** — expensive probes must justify their runtime.
- **The artifact is a first-class citizen** — design its schema with the
  downstream consumer in mind, not just the script's convenience.
- **Runs are additive** — never overwrite history, always append and derive.

---

## Future Phases (do not implement yet, but design for)

- GUI / dashboard for artifact visualization
- Multi-host aggregation (run on N machines, merge artifacts)
- Integration hook for upstream workflow orchestration
- Testing framework (pytest-based) for probe correctness
- User-facing progress and explainability layer (`rich` / `textual`)
