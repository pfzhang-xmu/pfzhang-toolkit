"""Platform differences kept in one place.

The booking core reads WeChat's local webview storage.  The path differs between
macOS and Windows; everything else (state directories, locks, sleep) is handled
here so the rest of the project stays platform-neutral.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_MACOS = sys.platform == "darwin"


def wechat_profiles_root() -> Path:
    """Return the directory that contains WeChat's ``webview_*`` profiles."""
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Tencent" / "xwechat" / "radium" / "web" / "profiles"
    return (Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents"
            / "app_data/radium/web/profiles")


def active_profiles(root: Path) -> set[Path]:
    """Profiles currently held open by WeChat.

    macOS can ask ``lsof``; on Windows the caller falls back to a disk scan.
    An empty set is a normal result (WeChat not running), not an error.
    """
    root = Path(root)
    if not IS_MACOS:
        return set()
    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-c", "WeChatAppEx", "-Fn"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Unable to identify active WeChat profiles") from exc
    if result.returncode not in (0, 1) or result.stderr or (result.returncode == 1 and result.stdout):
        raise RuntimeError("Unable to identify active WeChat profiles")
    active: set[Path] = set()
    for line in result.stdout.splitlines():
        if not line.startswith("n"):
            continue
        path = Path(line[1:])
        if not path.is_absolute():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if not relative.parts or not relative.parts[0].startswith("webview_"):
            continue
        profile = root / relative.parts[0]
        if profile.resolve().parent != root:
            raise RuntimeError("Invalid active profile")
        active.add(profile)
    return active


class KeepAwake:
    """Best-effort idle-sleep blocker while the scheduler is running.

    Closing the lid, manual sleep, or a power cut still stops the app.  The
    point is only to stop the machine from idling to sleep before 07:00.
    """

    def __init__(self) -> None:
        self._process = None
        self._previous = None

    def start(self) -> None:
        if IS_MACOS:
            try:
                self._process = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except OSError:
                self._process = None
        elif IS_WINDOWS:
            try:
                import ctypes
                kernel = getattr(ctypes, "windll").kernel32
                # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                self._previous = kernel.SetThreadExecutionState(0x80000000 | 0x00000001)
            except Exception:
                self._previous = None

    def stop(self) -> None:
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                pass
            self._process = None
        if IS_WINDOWS and self._previous is not None:
            try:
                import ctypes
                getattr(ctypes, "windll").kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass
            self._previous = None
