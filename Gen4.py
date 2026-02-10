#!/usr/bin/env python3
"""Gen4.py — IconMaker helper utilities (no UI)

Responsibilities
- Provide stable folder layout (re-exported from Gen2)
- Load app icon / title pixmap from assets in dev and PyInstaller runs
- Provide orphan-cleanup utilities for generated .ico output

Gen2 owns conversion logic; Gen4 intentionally avoids image processing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6 import QtCore, QtGui

import Gen2 as eng

# ----------------------------
# Folder layout (source of truth: Gen2)
# ----------------------------

ICONER_ROOT: Path = eng.ICONER_ROOT
ICON_IMAGES_DIR: Path = eng.ICON_IMAGES_DIR
ICONS_DIR: Path = eng.ICONS_DIR

LOGS_DIR: Path = ICONER_ROOT / "Logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# ----------------------------
# Assets (your explicit absolute paths)
# ----------------------------

APP_TITLE_IMAGE_ABS = r"C:\Users\Oluwatola Ayedun\Desktop\IconMaker\assets\Iconner.png"
APP_ICON_ICO_ABS = r"C:\Users\Oluwatola Ayedun\Desktop\IconMaker\assets\Iconner.ico"

# Fallback names if absolute paths are not found (dev/packaged)
ICON_DIR_CANDIDATES = ("assets", "Assets")
APP_TITLE_PNG_NAME = "Iconner.png"
APP_ICON_ICO_NAME = "Iconner.ico"


def _dev_dir() -> Path:
    return Path(__file__).resolve().parent


def _exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _meipass_dir() -> Optional[Path]:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        try:
            return Path(getattr(sys, "_MEIPASS"))
        except Exception:
            return None
    return None


def _candidate_base_dirs() -> list[Path]:
    bases: list[Path] = []
    mp = _meipass_dir()
    if mp:
        bases.append(mp)
    bases.append(_exe_dir())
    bases.append(_dev_dir())

    # De-dupe while preserving order
    out: list[Path] = []
    seen: set[str] = set()
    for b in bases:
        key = os.fspath(b)
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def _try_abs(path_str: str) -> Optional[Path]:
    if not path_str:
        return None
    p = Path(path_str)
    return p if p.is_file() else None


def find_asset(filename: str, *, abs_override: str = "") -> Optional[Path]:
    """Locate an asset in this priority order:

    1) abs_override (if valid)
    2) <_MEIPASS>/assets or Assets (PyInstaller)
    3) <exe_dir>/assets or Assets
    4) <dev_dir>/assets or Assets
    """
    p_abs = _try_abs(abs_override)
    if p_abs:
        return p_abs

    if not filename:
        return None

    for base in _candidate_base_dirs():
        for folder in ICON_DIR_CANDIDATES:
            p = base / folder / filename
            if p.is_file():
                return p
    return None


def _build_multi_size_icon(pm: QtGui.QPixmap) -> QtGui.QIcon:
    """Build a QIcon containing multiple sizes for taskbar/tray quality."""
    ico = QtGui.QIcon()
    for s in (256, 192, 128, 96, 64, 48, 40, 32, 24, 20, 16):
        ico.addPixmap(
            pm.scaled(
                s,
                s,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )
    return ico


def get_app_icon() -> QtGui.QIcon:
    """Return a QIcon for window/taskbar/tray.

    Priority:
    1) Your absolute Iconner.ico
    2) assets/Iconner.ico
    3) Iconner.png converted to multi-size icon
    4) theme fallback / empty
    """
    p_ico = find_asset(APP_ICON_ICO_NAME, abs_override=APP_ICON_ICO_ABS)
    if p_ico:
        ico = QtGui.QIcon(str(p_ico))
        if not ico.isNull():
            return ico

    p_png = find_asset(APP_TITLE_PNG_NAME, abs_override=APP_TITLE_IMAGE_ABS)
    if p_png:
        pm = QtGui.QPixmap(str(p_png))
        if not pm.isNull():
            return _build_multi_size_icon(pm)

    theme = QtGui.QIcon.fromTheme("application-icon")
    return theme if not theme.isNull() else QtGui.QIcon()


def get_title_pixmap() -> QtGui.QPixmap:
    """Pixmap used for the UI "mark" image."""
    p = find_asset(APP_TITLE_PNG_NAME, abs_override=APP_TITLE_IMAGE_ABS)
    if not p:
        return QtGui.QPixmap()
    pm = QtGui.QPixmap(str(p))
    return pm if not pm.isNull() else QtGui.QPixmap()


# ----------------------------
# Icons folder cleanup
# ----------------------------

def clean_icons_folder(
    log_fn: Callable[[str], None] | None,
    *,
    icons_dir: Path | None = None,
    images_dir: Path | None = None,
    remove_orphans: bool = True,
    orphan_action: str = "delete",
) -> int:
    """Remove/move orphan .ico files.

    Orphan definition: <stem>.ico exists in icons_dir but no matching source image stem
    exists anywhere under images_dir.

    orphan_action:
    - "delete" (default)
    - "trash"  (moves into icons_dir/_trash)

    Returns: number removed/moved.
    """
    if not remove_orphans:
        return 0

    icons_dir = Path(icons_dir or ICONS_DIR)
    images_dir = Path(images_dir or ICON_IMAGES_DIR)

    action = "trash" if str(orphan_action).lower() in ("trash", "recycle", "move") else "delete"

    try:
        return eng.remove_orphan_icons(
            images_dir=images_dir,
            icons_dir=icons_dir,
            action=action,
            logfn=log_fn,
        )
    except Exception as e:
        if log_fn:
            log_fn(f"[CLEAN][ERR] {e}")
        return 0
