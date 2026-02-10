#!/usr/bin/env python3
"""
IconMaker.py — Unified launcher
Runs:
    • Gen1 UI
    • Gen3 tray worker

Supports modes:
    --mode both (default)
    --mode ui
    --mode tray
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# ============================================================
# Windows Cairo DLL patch
# ============================================================

def _patch_cairo_dll_path() -> None:
    """Ensure libcairo is discoverable on Windows."""
    if os.name != "nt":
        return

    cairo_bin = r"C:\msys64\ucrt64\bin"
    if not os.path.isdir(cairo_bin):
        return

    # Python 3.8+ DLL directory support
    try:
        os.add_dll_directory(cairo_bin)  # type: ignore[attr-defined]
    except Exception:
        pass

    # Fallback PATH prepend
    os.environ["PATH"] = cairo_bin + os.pathsep + os.environ.get("PATH", "")


# ============================================================
# Qt plugin path patch
# ============================================================

from PySide6 import QtCore


def _patch_qt_plugin_path() -> None:
    """Ensure Qt can find platform plugins in dev & frozen runs."""
    base: Path | None = None

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS) / "PySide6" / "plugins"
    else:
        try:
            import PySide6
            base = Path(PySide6.__path__[0]) / "plugins"
        except Exception:
            base = None

    if base and base.exists():
        os.environ["QT_PLUGIN_PATH"] = str(base)

        paths = QtCore.QCoreApplication.libraryPaths()
        if str(base) not in paths:
            QtCore.QCoreApplication.setLibraryPaths([str(base), *paths])


# ============================================================
# Argument parsing
# ============================================================

def parse_mode(argv: list[str]) -> str:
    """Return run mode from CLI args."""
    for i, arg in enumerate(argv):
        if arg == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1].lower().strip()
            if mode in {"both", "ui", "tray"}:
                return mode
    return "both"


# ============================================================
# Runtime helpers
# ============================================================

def is_frozen() -> bool:
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def script_path() -> Path:
    try:
        return Path(__file__).resolve()
    except Exception:
        return Path(sys.argv[0]).resolve()


# ============================================================
# Single-instance tray mutex (Windows)
# ============================================================

def tray_already_running() -> bool:
    """Prevent duplicate tray instances."""
    if os.name != "nt":
        return False

    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, "Global\\IconMakerTrayMutex")

        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            if handle:
                kernel32.CloseHandle(handle)
            return True

        return False
    except Exception:
        return False


# ============================================================
# Process spawning
# ============================================================

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def spawn_detached(args: list[str], cwd: str | None = None) -> None:
    """Spawn a detached background process."""
    if os.name == "nt":
        subprocess.Popen(
            args,
            close_fds=True,
            cwd=cwd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
    else:
        subprocess.Popen(args, close_fds=True, cwd=cwd)


def tray_command() -> list[str]:
    """
    Build correct tray launch command.

    Frozen:
        IconMaker.exe --mode tray

    Dev:
        <venv python> IconMaker.py --mode tray
    """
    if is_frozen():
        return [sys.executable, "--mode", "tray"]

    return [sys.executable, str(script_path()), "--mode", "tray"]


# ============================================================
# Run targets
# ============================================================

def run_ui() -> None:
    _patch_cairo_dll_path()
    _patch_qt_plugin_path()

    from Gen1 import main as gen1_main
    gen1_main()


def run_tray() -> None:
    _patch_cairo_dll_path()
    _patch_qt_plugin_path()

    from Gen3 import main as gen3_main
    gen3_main()


# ============================================================
# Main launcher
# ============================================================

def main() -> None:
    _patch_cairo_dll_path()

    mode = parse_mode(sys.argv)

    # ---- Tray only ----
    if mode == "tray":
        if tray_already_running():
            return
        run_tray()
        return

    # ---- UI only ----
    if mode == "ui":
        run_ui()
        return

    # ---- BOTH (default) ----
    if not tray_already_running():
        spawn_detached(tray_command(), cwd=str(script_path().parent))

    run_ui()


if __name__ == "__main__":
    main()
