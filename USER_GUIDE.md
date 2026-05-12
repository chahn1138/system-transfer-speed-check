# User Guide — system-transfer-speed-check

## What This Tool Does

`system-transfer-speed-check` is a cross-platform Python toolkit that measures,
diagnoses, and accumulates intelligence about file transfer performance. It runs
on Windows, macOS, and Linux, using native tools on each platform for full
fidelity — not lowest-common-denominator abstractions.

It is not a one-shot benchmark. Every run appends results to a structured JSON
artifact. Over time the artifact builds a complete picture of what your hardware
and network can actually do — and why transfers may be slower than expected.

> *"The code stays clean. The learning lives in the artifact."*

---

## Requirements

| Requirement | Notes |
|-------------|-------|
| Python 3.9 or later | 3.11+ recommended |
| `psutil` | `pip install psutil` — required for Layer 5 live telemetry |
| `rich` | `pip install rich` — required for color console output |
| Git | For syncing results between machines |

Install all Python dependencies at once:

```
pip install -r requirements.txt
```

---

## Quick Start

### Run all five probe layers on this machine

```
python run_probes.py
```

This takes under 2 minutes. Results are saved to `artifact/hosts/<hostname>.json`.

### View this machine's results

```
python run_probes.py --summary
```

### Compare results across all machines

```
python run_probes.py --compare
```

All JSON files in `artifact/hosts/` are read and displayed side-by-side.

---

## Command Reference

| Flag | Description |
|------|-------------|
| *(none)* | Run all 5 probe layers and save results |
| `--layers hardware` | Run only the specified layer(s) |
| `--layers hardware,network` | Run multiple specific layers |
| `--summary` | Display this machine's artifact and exit |
| `--compare` | Display a cross-host comparison table and exit |
| `--reset` | Wipe this machine's artifact and start fresh |
| `--target <host>` | Set a remote target for network protocol tests |
| `--payload-mb <n>` | Override the test payload size (default: 256 MB) |

### `--layers` values

| Value | What it runs |
|-------|-------------|
| `hardware` | Layer 1 — disk, CPU, RAM, NIC speed, power plan |
| `network` | Layer 2 — ping, MTU, Wi-Fi, DNS, TCP window, traceroute |
| `protocols` | Layer 3 — python-copy, robocopy/rsync, scp, rsync-ssh |
| `tuning` | Layer 4 — block size, thread count, compression, sync mode, file profile sweeps |
| `live` | Layer 5 — live CPU/disk/NIC telemetry during transfer scenarios |
| `all` | All five layers (default) |

---

## The Five Probe Layers

### Layer 1 — Hardware Baseline

*What is the physical ceiling before any transfer begins?*

Measures the raw capabilities of the local machine:

- **Disk sequential read/write** — the sustained throughput ceiling
- **CPU** — model, core count, AES-NI hardware crypto acceleration
- **RAM** — total available memory
- **NICs** — active interfaces and their negotiated link speed
- **Power plan** (Windows) — detects if "Balanced" mode is silently throttling performance

### Layer 2 — Network Characterization

*What does the pipe actually look like?*

- **Ping / jitter** — baseline round-trip time; high jitter kills TCP efficiency
- **MTU discovery** — detects fragmentation from misconfigured jumbo frames
- **Wi-Fi** — band (2.4/5/6 GHz), RSSI signal strength, TX rate
- **DNS timing** — hidden latency tax on per-connection workflows
- **TCP window auto-tuning** — confirms the OS is scaling receive windows
- **Bandwidth-Delay Product** — calculates the ideal TCP window for this path
- **Traceroute** — shows where latency lives (LAN, router, WAN)

### Layer 3 — Protocol Benchmarks

*Which transfer tool is actually fastest for this hardware and route?*

Runs the same test payload through each available tool and records throughput:

| Protocol | Platform | Notes |
|----------|----------|-------|
| `python-copy` | All | Pure Python shutil + fsync; OS copy path baseline |
| `robocopy` | Windows | Native multi-threaded; often fastest for Windows-to-Windows |
| `rsync` | macOS / Linux | Checksum overhead on first run; excellent for incremental |
| `scp` | All (requires `--target`) | Encrypted single-stream; CPU-bound without AES-NI |
| `rsync-ssh` | All (requires `--target`) | Encrypted rsync over SSH |
| `robocopy-unc` | Windows (requires `--target`) | Multi-threaded network copy via UNC path |

Network protocols require `--target <hostname>`:

```
python run_probes.py --layers protocols --target myserver.local
```

### Layer 4 — Tuning Sweeps

*Given this hardware and protocol, what configuration extracts the most?*

Runs five automated sweeps and records throughput at each setting:

| Sweep | What it finds |
|-------|--------------|
| **Block size** | Optimal write chunk size (4 KB → 8 MB) |
| **Thread count** | Thread count where adding more stops helping (1/2/4/8) |
| **Compression** | Whether compressing helps or hurts for this data type |
| **Sync mode** | Buffered vs fsync-per-write vs fsync-at-end cost |
| **File profile** | Many small files vs few large files (same total bytes) |

Results are used to automatically derive `bottleneck_hints`.

### Layer 5 — Live Telemetry

*What is the machine actually doing during a transfer?*

Runs three transfer scenarios while sampling system metrics at 1-second intervals:

| Scenario | Description |
|----------|-------------|
| `single_stream_buffered` | One large sequential write |
| `four_stream_parallel` | Four parallel writers |
| `small_file_stress` | Many small files — worst case for metadata overhead |

**Metrics captured per scenario:**

- CPU % (peak)
- Disk write throughput MB/s (peak)
- NIC TX/RX MB/s
- Memory available GB (minimum — detects buffer pressure)

---

## Multi-Machine Workflow

The artifact store is designed to accumulate results from multiple machines and
compare them side-by-side. Git is used as the sync bus.

### Setup on each machine

```bash
git clone https://github.com/<your-repo>/system-transfer-speed-check
cd system-transfer-speed-check
pip install -r requirements.txt
python run_probes.py
git add artifact/hosts/<hostname>.json
git commit -m "chore: <hostname> probe run"
git push
```

### Compare across machines

On any machine, after pulling:

```bash
git pull
python run_probes.py --compare
```

Each host's JSON is read from `artifact/hosts/`. The comparison table shows
disk, CPU, RAM, NICs, live telemetry, tuning sweep winners, and protocol
benchmarks — all side-by-side, with the best value highlighted.

---

## The Artifact

Results are stored in `artifact/hosts/<hostname>.json`. The file is
**append-only** — each run adds to `run_history` and updates each section with
the latest measurement. History is never deleted unless you run `--reset`.

### Top-level sections

| Section | Contents |
|---------|---------|
| `hardware_baseline` | Disk, CPU, RAM, NIC measurements |
| `network_topology` | Gateway, ping, MTU, Wi-Fi, DNS, TCP, BDP, traceroute |
| `protocol_results` | Per-protocol throughput records |
| `tuning_results` | Per-sweep throughput records |
| `live_results` | Per-scenario telemetry records |
| `bottleneck_hints` | Derived conclusions with confidence and suggested action |
| `run_history` | Timestamped log of every run |

### Bottleneck hints

The script automatically writes bottleneck hints when measured values fall
outside expected ranges. Each hint includes:

- **Layer** — which probe layer detected the issue
- **Observation** — what was measured vs. what was expected
- **Confidence** — `high`, `medium`, or `low`
- **Suggested action** — specific remediation step

Example:

```json
{
  "layer": "hardware.disk",
  "observation": "Seq write 245 MB/s — below 500 MB/s threshold for SSD",
  "confidence": "medium",
  "suggested_action": "Check power plan; verify no AV scan active during test"
}
```

---

## Maintenance

### Remove sentinel / error records from an artifact

```
python tools/clean_artifact.py
```

Dry-run by default — shows what would be removed without changing anything.

```
python tools/clean_artifact.py --apply
```

Writes the cleaned artifact. Use `--hosts-dir <path>` to override the default
`artifact/hosts/` directory.

### Reset this machine's artifact

```
python run_probes.py --reset
```

Deletes the current host's JSON and starts fresh. Does not affect other hosts.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `ModuleNotFoundError: psutil` | psutil not installed in active Python env | `python -m pip install psutil` |
| `ModuleNotFoundError: rich` | rich not installed | `python -m pip install rich` |
| Wi-Fi shows "speed unknown" | Wi-Fi not associated when Layer 2 ran | Re-run `--layers network` while connected |
| rsync shows no result on Windows | rsync is not available on Windows | Expected — rsync runs on macOS/Linux only |
| robocopy shows no result on Mac | robocopy is Windows-only | Expected |
| `--target` tests show `—` | No `--target` was provided | Pass `--target <hostname>` when running Layer 3 |
| Disk write speed looks low | "Balanced" power plan active (Windows) | Switch to High Performance: `powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c` |

---

## File Structure

```
run_probes.py              Entry point — orchestrates all layers
probe/
  hardware.py              Layer 1 — disk, CPU, RAM, NIC, power plan
  network.py               Layer 2 — ping, MTU, Wi-Fi, DNS, TCP, traceroute
  protocols.py             Layer 3 — protocol benchmarks
  tuning.py                Layer 4 — tuning sweeps
  live.py                  Layer 5 — live telemetry sampler
  platform_utils.py        OS detection, subprocess helpers
artifact/
  schema.py                Artifact schema definition
  writer.py                Load/save artifact, append_run, add_bottleneck_hint
  aggregate.py             Multi-host comparison builder
  hosts/
    <hostname>.json        Per-host artifact (one file per machine)
report/
  summarize.py             Plain-text single-host summary (stdlib only)
  rich_summary.py          Rich-formatted single-host summary
  rich_compare.py          Rich-formatted multi-host comparison
tools/
  clean_artifact.py        Remove sentinel/error records from artifact files
requirements.txt           psutil, rich
```

---

## Platform Notes

### Windows
- Uses PowerShell CIM queries for hardware info
- `robocopy` is the native multi-threaded copy tool
- Power plan detection warns if "Balanced" mode is active
- `rsync` and `scp` require WSL or a third-party install (not tested)

### macOS
- Uses `system_profiler`, `diskutil`, `sysctl` for hardware info
- Wi-Fi data via `system_profiler SPAirPortDataType` (requires Wi-Fi to be associated at run time)
- `airport` binary removed in Sonoma/Sequoia — the tool does not rely on it
- `rsync` is the native fast-copy tool

### Linux
- Uses `fio`, `hdparm`, `dd`, `/proc/cpuinfo` for hardware info
- `ethtool`, `ip link`, `iw` for network info
- Tested structure; full validation on a third host pending
