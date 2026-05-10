# system-transfer-speed-check

A cross-platform Python toolkit for measuring, diagnosing, and continuously
learning about file transfer performance across Windows, Linux, and macOS.

---

## Goals

- **Baseline** every layer of the transfer stack — from raw disk I/O to NIC
  throughput — so you always know the physical ceiling before judging any
  measured result.
- **Detect bottlenecks** across methods (threading models), tools (`scp`,
  `rsync`, `robocopy`, `rclone`, `netcat`, …), hardware layouts, and network
  paths.
- **Accumulate intelligence** about your systems in a persistent JSON artifact
  that grows smarter with every run and can be fed as structured input into
  downstream workflows.

---

## Supported Platforms

| Platform | Native Tooling Mindset |
|----------|------------------------|
| **Windows** | PowerShell, `robocopy`, WinRM, WMI/CIM, `netsh`, `Get-NetAdapter` |
| **Linux** | `bash`, `rsync`, `iperf3`, `ethtool`, `ss`, `ip`, `hdparm`, `fio` |
| **macOS** | `zsh`/`bash`, `rsync`, `iperf3`, `networksetup`, `system_profiler`, `diskutil` |

Python is the unifying runtime across all three. Native OS commands are called
out per-platform and invoked via Python's `subprocess` with OS detection.

---

## Repository Structure

```
system-transfer-speed-check/
├── README.md                   ← this file
├── PROMPT.md                   ← comprehensive prompt / design brief
├── PROJECT_DESCRIPTION.md      ← detailed layer-by-layer technical design
├── Prompt.txt                  ← original seed prompt
│
├── probe/
│   ├── __init__.py
│   ├── hardware.py             ← Layer 1: disk, CPU, RAM, PCIe probes
│   ├── network.py              ← Layer 2: NIC, ping, MTU, iperf3, DNS
│   ├── protocols.py            ← Layer 3: per-tool transfer benchmarks
│   ├── tuning.py               ← Layer 4: threads, block size, compression
│   ├── live.py                 ← Layer 5: in-flight system state capture
│   └── platform_utils.py       ← OS detection + native command dispatch
│
├── artifact/
│   ├── schema.py               ← JSON artifact schema definition
│   ├── writer.py               ← artifact update / merge logic
│   └── transfer-profile.json   ← THE living intelligence artifact (gitignored or versioned)
│
├── report/
│   └── summarize.py            ← human-readable console/HTML summary
│
├── run_probes.py               ← main entry point
└── requirements.txt
```

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run all probes on this machine (auto-detects OS)
python run_probes.py

# Run only network probes
python run_probes.py --layers network

# Run a specific protocol comparison
python run_probes.py --layers protocols --target user@remote-host

# Show the current intelligence artifact as a readable summary
python report/summarize.py
```

---

## The Intelligence Artifact

Every probe run appends to `artifact/transfer-profile.json`. This file:

- **Starts naive** — no assumptions, all fields populated as probes run.
- **Accumulates over time** — each run adds a timestamped entry to
  `run_history`.
- **Derives conclusions** — the `bottleneck_hints` section is written by the
  script's own analysis of measured numbers vs. baselines.
- **Is designed to be consumed** — by the next step in a larger workflow,
  passed in as structured JSON input.

See `PROJECT_DESCRIPTION.md` for the full schema design.

---

## Philosophy

> *"There will have to be a starting phase when actions are done independent of
> whether they are expected to be fast or slow — because the system could not
> know as it starts."*

The first run is always a discovery run. Speed is never assumed. Every
subsequent run is informed by prior results. The code stays clean; the
learning lives in the artifact.

---

## Contributing / Extending

- Each OS has a **fully native mindset** — don't homogenize where native tools
  are clearly superior. Wrap them, don't replace them.
- New probes go into the appropriate `probe/` module and register themselves
  in the artifact schema.
- Overhead warnings are documented inline — some probes (e.g. `iperf3`,
  antivirus impact test) require controlled conditions and are opt-in.

---

## Roadmap

- [ ] Phase 1: Local hardware baseline + network characterization (all 3 OSes)
- [ ] Phase 2: Protocol comparison matrix
- [ ] Phase 3: Multi-threaded tuning sweeps
- [ ] Phase 4: Live in-flight telemetry during transfers
- [ ] Phase 5: GUI / dashboard for artifact visualization
- [ ] Phase 6: Integration with upstream workflow orchestration
