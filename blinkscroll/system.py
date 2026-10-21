"""Small Windows helpers: lock-screen detection, single instance, DPI awareness, autostart."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .config import FROZEN, PROJECT_DIR

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"
SHORTCUT = STARTUP_DIR / "Blink Scroll.lnk"


def enable_dpi_awareness() -> None:
    """Use real pixels everywhere so widget, cursor and screen coordinates agree."""
    if not _IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def is_session_locked() -> bool:
    """True while the Windows lock screen is showing (its LockApp.exe owns the foreground)."""
    if not _IS_WINDOWS:
        return False
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False  # e.g. during desktop switches; treat as unlocked
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return False
        return buf.value.lower().endswith("\\lockapp.exe")
    finally:
        kernel32.CloseHandle(handle)


class SingleInstance:
    """Named mutex so a second copy does not fight over the camera."""

    def __init__(self, name: str = "BlinkScroll.SingleInstance") -> None:
        self._handle = None
        self.already_running = False
        if not _IS_WINDOWS:
            return
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(self._handle)
            self._handle = None


# ----------------------------------------------------------------------------- autostart


def _launch_command() -> tuple[Path, str, Path]:
    """(target, arguments, working dir) that start the app without a console."""
    exe = Path(sys.executable)
    if FROZEN:  # installed app: BlinkScroll.exe itself
        return exe, "", exe.parent
    pythonw = exe.with_name("pythonw.exe")
    return (pythonw if pythonw.exists() else exe), "-m blinkscroll", PROJECT_DIR


def autostart_enabled() -> bool:
    return SHORTCUT.exists()


def set_autostart(enabled: bool) -> None:
    """Create/remove a Startup-folder shortcut that launches the app without a console."""
    if not enabled:
        SHORTCUT.unlink(missing_ok=True)
        log.info("Autostart disabled")
        return
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BS_LNK);"
        "$s.TargetPath = $env:BS_TARGET; $s.Arguments = $env:BS_ARGS;"
        "$s.WorkingDirectory = $env:BS_DIR; $s.Description = 'Blink Scroll'; $s.Save()"
    )
    target, args, workdir = _launch_command()
    env = dict(os.environ, BS_LNK=str(SHORTCUT), BS_TARGET=str(target), BS_ARGS=args,
               BS_DIR=str(workdir))
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                   check=True, capture_output=True, creationflags=0x08000000)  # CREATE_NO_WINDOW
    log.info("Autostart enabled: %s", SHORTCUT)


def open_file(path: Path) -> None:
    os.startfile(path)  # type: ignore[attr-defined]  # Windows only
