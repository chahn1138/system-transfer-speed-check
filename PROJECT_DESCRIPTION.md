# Project Description — system-transfer-speed-check

## Overview

This project is a layered, cross-platform Python toolkit for understanding,
measuring, and continuously improving file transfer performance. It is not a
one-shot benchmarking script — it is a **learning system** that accumulates
knowledge about your hardware and network environment in a structured JSON
artifact, designed to be consumed by downstream workflows.

The system treats Windows, Linux, and macOS as equal first-class targets,
each addressed with a fully native mindset.

---

## The Five Probe Layers

### Layer 1 — Local Hardware Baseline

*What is the physical ceiling before any transfer begins?*

| Probe | What it tells you | Overhead |
|-------|-------------------|----------|
| Disk sequential read/write (large block) | Sustained throughput ceiling | Low |
| Disk random read/write (IOPS) | Small-file transfer capability | Low |
| Disk queue depth saturation | Controller saturation point | Low |
| RAM bandwidth | In-memory buffer ceiling | Low |
| CPU AES-NI hardware crypto check | SSH/SCP CPU cost near-zero if present | Low |
| PCIe lane check (NVMe vs SATA path) | Shared-lane saturation trap detection | Low |
| Windows Power Plan detection | "Balanced" silently throttles NIC + disk | Near-zero |
| Antivirus / real-time scan impact | Can cut throughput 30–60%; requires controlled test | ⚠️ Opt-in |

**Native tools by OS:**
- Windows: `diskspd`, WMI/CIM disk info, `Get-NetAdapter`, PowerShell CIM queries
- Linux: `fio`, `hdparm`, `dd`, `/proc/cpuinfo` AES-NI flag, `lspci`
- macOS: `diskutil`, `system_profiler SPStorageDataType`, `sysctl hw.optional.aes`

---

### Layer 2 — Network Characterization

*What does the pipe actually look like — and where does it lie?*

| Probe | What it tells you | Overhead |
|-------|-------------------|----------|
| Ping (latency + jitter) | Baseline RTT; jitter kills TCP window efficiency | Near-zero |
| Traceroute / tracepath | Where latency lives (LAN, router, WAN) | Low |
| MTU / Jumbo Frame discovery | Fragmentation from misconfigured jumbo frames | Low |
| NIC negotiated speed vs. advertised | 10GbE linking at 1GbE is common and silent | Near-zero |
| Wi-Fi band, RSSI, channel congestion | 2.4 / 5 / 6 GHz; signal quality; interference | Low |
| **iperf3 TCP** | Raw pipe capacity — the gold standard | ⚠️ Opt-in (needs server) |
| **iperf3 UDP** | Packet loss independent of TCP retransmit | ⚠️ Opt-in (needs server) |
| Bandwidth-Delay Product (BDP) | Ideal TCP window size for this path (calculated) | Zero (derived) |
| TCP window auto-tuning check | Is the OS actually scaling windows? | Near-zero |
| Packet loss rate | 0.1% loss collapses TCP on high-latency paths | Low |
| DNS resolution time | Hidden tax when connections re-resolve per session | Near-zero |
| Switch port error counters | CRC errors, runts, giants from bad cables/SFPs | Near-zero |

**Native tools by OS:**
- Windows: `ping`, `tracert`, `netsh interface ipv4`, `Get-NetAdapter`, `netsh wlan`
- Linux: `ping`, `tracepath`, `ip link`, `ethtool`, `iwconfig`/`iw`, `ss`
- macOS: `ping`, `traceroute`, `networksetup`, `airport -I`, `netstat`

---

### Layer 3 — Transfer Protocol Comparison

*Which tool, for this route, on this hardware, is actually fastest?*

| Tool | Notes | Overhead |
|------|-------|----------|
| `netcat` / `ncat` | Raw, zero-encryption baseline — theoretical max | Low |
| `scp` | Single-stream, encrypted; CPU-bound without AES-NI | Low |
| `sftp` | Like scp + resume support; slightly more overhead | Low |
| `rsync` (first run) | Checksum overhead; excellent on repeat transfers | ⚠️ First-run cost |
| `rsync` (incremental) | Delta sync — transforms repeat transfers entirely | Low |
| `robocopy` (Windows) | Native multi-threaded (`/MT:n`); often fastest W→W | Low |
| `rclone` | Multi-threaded, multi-chunk, cloud-aware; excellent tuning surface | Low |
| `bbcp` | Parallel SCP; major win on high-BDP WAN links | Medium to configure |
| SMB (version check) | SMB3 multichannel vs SMB1 disaster; what's negotiating? | Near-zero |
| HTTP/HTTPS (`curl`) | Useful TLS-overhead comparison point vs SSH | Low |

Each tool is run against the same test payload under identical conditions so
results are directly comparable within a single run.

---

### Layer 4 — Transfer Tuning Probes

*Given the hardware and protocol, what configuration extracts the most?*

| Probe / Test | What it tells you |
|--------------|-------------------|
| Single-stream vs. multi-stream throughput curve | Thread count where adding more stops helping |
| Chunk / block size sweep | Optimal block size varies by disk+network combo |
| Compression on vs. off | Helps on WAN with compressible data; hurts on LAN or pre-compressed files |
| Encryption on vs. off | Quantifies the SSH/TLS tax; relevant for trusted internal transfers |
| Sync vs. async I/O | Buffered vs. unbuffered writes at the destination |
| Parallel stream count sweep (rclone/bbcp) | Optimal parallelism for this pipe |
| File count vs. file size tradeoff | Many small files vs. fewer large ones — very different profiles |

Results feed directly into `bottleneck_hints` in the artifact.

---

### Layer 5 — Live System State During Transfer

*What is the machine doing while the transfer runs?*

| Metric captured | Why it matters |
|-----------------|----------------|
| CPU % per-core | Is encryption/compression pinning one core? |
| NIC bytes/sec (tx + rx) | Confirm link saturation vs. expected |
| Disk I/O queue depth | Destination disk saturation |
| Network error/drop counters | Retransmits invisible to speed but real in reliability |
| Memory pressure / buffer cache | Write stalls from buffer eviction |

Captured via `psutil` (cross-platform) + native tools at 1-second intervals
during the transfer window, then correlated with throughput measurements.

---

## The Intelligence Artifact

### Philosophy

> *"The code stays clean. The learning lives in the artifact."*

The artifact (`transfer-profile.json`) is a **first-class citizen** of this
project — not a log file, not a side effect. It is designed to be:

- **Consumed** by downstream workflows as structured JSON input
- **Accumulated** over time — each run appends, never overwrites history
- **Self-analyzing** — `bottleneck_hints` are written by the script's own
  comparison of measured values against baselines
- **Naive at start** — no assumptions on first run; everything is discovered

### Top-Level Schema Sections

| Section | Purpose |
|---------|---------|
| `hardware_baseline` | Physical ceiling measurements per device |
| `network_topology` | Discovered routes, interfaces, Wi-Fi state |
| `protocol_results` | Per-tool, per-route benchmark results |
| `bottleneck_hints` | Derived conclusions with confidence and suggested action |
| `run_history` | Timestamped log of every run, layers executed, and summary |

### Bottleneck Hint Structure

```json
{
  "timestamp": "2026-05-10T14:32:00Z",
  "layer": "network",
  "observation": "TCP throughput (180 MBps) is 43% below iperf3 raw pipe (320 MBps)",
  "confidence": "high",
  "suggested_action": "Check TCP window scaling; BDP for this path is 4.8 MB, current window may be undersized"
}
```

---

## Probe Overhead Policy

Probes are classified by their cost-to-value ratio:

| Class | Default | Examples |
|-------|---------|---------|
| **Passive / near-zero** | Always run | NIC speed, CPU flags, ping, DNS |
| **Active / low** | Always run | Disk sequential test, traceroute, protocol benchmarks |
| **Active / medium** | ⚠️ Opt-in | iperf3 (needs server), full fio sweep |
| **Controlled / disruptive** | ⚠️ Opt-in | AV impact test, large destructive write tests |

A default run completes in under 2 minutes on any supported platform.

---

## Cross-Platform Design Pattern

```
run_probes.py
  └── probe/platform_utils.py
        ├── detect_os() → "windows" | "linux" | "macos"
        └── dispatch(probe_name) → calls OS-specific implementation

probe/hardware.py
  ├── _hardware_windows()   ← WMI, diskspd, PowerShell CIM
  ├── _hardware_linux()     ← fio, hdparm, /proc/cpuinfo
  └── _hardware_macos()     ← diskutil, system_profiler, sysctl
```

Each OS implementation is self-contained. No lowest-common-denominator
abstractions that weaken any platform's native capability.

---

## Roadmap

### Phase 1 — Foundation *(current)*
- Project structure, artifact schema, platform dispatch
- Layer 1 (hardware baseline) on all 3 OSes
- Layer 2 (network characterization) on all 3 OSes

### Phase 2 — Protocol Matrix
- Layer 3: per-tool benchmarks across all supported transfer tools
- Artifact `protocol_results` population and comparison

### Phase 3 — Tuning Sweeps
- Layer 4: thread count, block size, compression, encryption sweeps
- Automated derivation of `bottleneck_hints`

### Phase 4 — Live Telemetry
- Layer 5: `psutil` + native counter capture during transfers
- Correlation engine: throughput vs. system state

### Phase 5 — Visualization & UX
- `rich` / `textual` console dashboard
- HTML report from artifact
- GUI (platform-native or web-based TBD)

### Phase 6 — Integration
- Upstream workflow hook (artifact as input to next action)
- Multi-host artifact aggregation
- `pytest`-based probe correctness test suite

---

## Key Design Decisions & Rationale

| Decision | Rationale |
|----------|-----------|
| Python as unifying runtime | Best cross-platform reach; rich ecosystem; user's stated preference |
| Native tools per OS, not universal wrappers | Preserves each platform's full capability; avoids lowest-common-denominator |
| JSON artifact (not SQLite, not CSV) | Widely supported; human-readable; easily consumed by downstream tools |
| Opt-in for expensive probes | Overhead must be earned; default run must be fast and safe |
| Artifact is append-only | History is data; trends over time reveal what snapshots cannot |
| Bottleneck hints are derived, not hardcoded | The script's analysis must be reproducible and auditable |
