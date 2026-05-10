"""
probe/platform_utils.py
=======================
OS detection, subprocess helpers, and native command dispatch.

Every other probe module imports from here rather than calling
subprocess or platform directly.
"""

import platform
import shutil
import subprocess
from typing import List, Optional, Tuple, Union


# ── OS Detection ─────────────────────────────────────────────────────────────

def detect_os() -> str:
    """Return 'windows', 'linux', or 'macos'."""
    s = platform.system().lower()
    if s == "windows":
        return "windows"
    if s == "darwin":
        return "macos"
    return s  # 'linux' or other POSIX


def hostname() -> str:
    return platform.node()


def os_version() -> str:
    return platform.version()


def python_version() -> str:
    return platform.python_version()


# ── Subprocess helpers ────────────────────────────────────────────────────────

def run_command(
    cmd: Union[List[str], str],
    timeout: int = 30,
    shell: bool = False,
) -> Tuple[str, str, int]:
    """
    Run a subprocess command.

    Returns (stdout, stderr, returncode).
    Never raises — returns ("", error_message, -1) on any failure.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except FileNotFoundError:
        name = cmd[0] if isinstance(cmd, list) else str(cmd).split()[0]
        return "", f"Command not found: {name}", -1
    except Exception as e:
        return "", str(e), -1


def ps(command: str, timeout: int = 30) -> Tuple[str, str, int]:
    """
    Run a PowerShell command (Windows only).

    Uses -NoProfile and -NonInteractive to keep startup fast and
    avoid interactive prompts interfering with output capture.
    """
    return run_command(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        timeout=timeout,
    )


def tool_available(name: str) -> Optional[str]:
    """Return full path of tool if available on PATH, else None."""
    return shutil.which(name)
