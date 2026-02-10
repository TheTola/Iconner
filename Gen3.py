#!/usr/bin/env python3
# Gen3.py — IconMaker Background Tray Worker (tray-only, no UI)

from __future__ import annotations

import os
import sys
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets, QtNetwork

import Gen2 as eng
from Gen4 import (
    get_app_icon,
    clean_icons_folder,
    ICON_IMAGES_DIR,
    ICONS_DIR,
    LOGS_DIR,
)

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker_Gen3"

TRAY_IPC_NAME = "IconMaker_TrayIPC"

ALL_SIZES: List[int] = list(range(8, 257, 8))
SCAN_INTERVAL_MS = 10 * 60 * 1000  # 10 minutes

LOG_FILE = LOGS_DIR / "tray.log"
LOG_MAX_BYTES = 2_000_000


# ----------------------------
# Small utilities
# ----------------------------

def _pre_app_setup() -> None:
    """Windows taskbar grouping / AppUserModelID."""
    if sys.platform.startswith("win"):
        try:
            import ctypes  # type: ignore
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ORG}.{APP_NAME}")
        except Exception:
            pass


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _venv_scripts_dir() -> Path:
    """
    Best-effort path to the venv Scripts directory if running from source.
    """
    # sys.prefix points at venv root when activated / running venv python
    return Path(sys.prefix).resolve() / ("Scripts" if sys.platform.startswith("win") else "bin")


def _rotate_log_if_needed() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            bak = LOGS_DIR / "tray.log.1"
            try:
                bak.unlink(missing_ok=True)
            except Exception:
                pass
            LOG_FILE.rename(bak)
    except Exception:
        pass


def _log(msg: str) -> None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_log_if_needed()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with LOG_FILE.open("a", encoding="utf-8", errors="ignore") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _iter_images_recursive(root: Path) -> List[Path]:
    if not root.exists():
        return []
    out: List[Path] = []
    try:
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in eng.IMAGE_EXTS:
                out.append(p)
    except Exception:
        return []
    return out


def _qsettings() -> QtCore.QSettings:
    return QtCore.QSettings(APP_ORG, APP_NAME)


def _load_watch_folders() -> List[str]:
    raw = _qsettings().value("watch_folders", [], type=list)
    out: List[str] = []
    for x in raw or []:
        try:
            s = str(x).strip()
            if s:
                out.append(s)
        except Exception:
            continue
    # de-dupe preserve order
    seen = set()
    deduped: List[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped


def _save_watch_folders(paths: List[str]) -> None:
    # normalize, de-dupe, preserve order
    cleaned: List[str] = []
    seen = set()
    for p in paths:
        s = str(p).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        cleaned.append(s)
    _qsettings().setValue("watch_folders", cleaned)


def _get_notifications_enabled() -> bool:
    return _qsettings().value("notifications", True, type=bool)


def _set_notifications_enabled(v: bool) -> None:
    _qsettings().setValue("notifications", bool(v))


def _get_paused() -> bool:
    return _qsettings().value("paused", False, type=bool)


def _set_paused(v: bool) -> None:
    _qsettings().setValue("paused", bool(v))


def _unique_existing(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for s in items:
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


# ----------------------------
# Scan / convert
# ----------------------------

@dataclass(frozen=True)
class ScanResult:
    scanned: int
    converted: int
    deleted_orphans: int
    mirrored_into_library: int


def scan_and_convert(*, autocrop: bool = False, padding_mode: str = "balanced") -> ScanResult:
    """
    - Imports (copies) images from watched folders into ICON_IMAGES_DIR
    - Normalizes library naming/layout
    - Removes orphan .ico files
    - Creates/updates .ico for each library image

    Returns a ScanResult.
    """
    mirrored = 0

    # 0) Pull from extra watched folders into library (copy only)
    watched = [Path(p) for p in _load_watch_folders()]
    for root in watched:
        if not root.exists() or not root.is_dir():
            continue
        try:
            for img in _iter_images_recursive(root):
                dst = eng.mirror_copy_to_icon_images(img)
                if dst:
                    mirrored += 1
        except Exception as e:
            _log(f"Watch import ERR: {root} -> {e}")

    if mirrored:
        _log(f"Watch import: copied {mirrored} file(s) into library.")

    # 1) Normalize the library (flatten + rename)
    try:
        changes = eng.normalize_icon_images_library(logfn=_log)
        if changes:
            _log(f"Library normalized: {changes} change(s).")
    except Exception as e:
        _log(f"Normalize ERR: {e}")

    # 2) Delete orphan icons
    before = set(p.name for p in ICONS_DIR.glob("*.ico"))
    try:
        clean_icons_folder(
            _log,
            src_dir=ICON_IMAGES_DIR,
            out_dir=ICONS_DIR,
            remove_orphans=True,
            suffix="",
        )
    except Exception as e:
        _log(f"Clean ERR: {e}")
    after = set(p.name for p in ICONS_DIR.glob("*.ico"))
    deleted = max(0, len(before) - len(after))

    # 3) Convert/update icons for library root
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    converted = 0
    scanned = 0

    if not ICON_IMAGES_DIR.exists():
        _log("Scan skipped: ICON_IMAGES_DIR does not exist.")
        return ScanResult(scanned=0, converted=0, deleted_orphans=deleted, mirrored_into_library=mirrored)

    for img in ICON_IMAGES_DIR.iterdir():
        if not img.is_file() or img.suffix.lower() not in eng.IMAGE_EXTS:
            continue

        scanned += 1
        eng.make_ico(img, ICONS_DIR)

        try:
            needs_build = (not dst.exists())
            if not needs_build:
                try:
                    needs_build = img.stat().st_mtime > dst.stat().st_mtime
                except Exception:
                    needs_build = True

            if needs_build:
                ok, message = eng.make_ico(
                    img,
                    dst,
                    sizes=ALL_SIZES,
                    keep_alpha=True,
                    autocrop=autocrop,
                    padding_mode=padding_mode,
                )
                if ok:
                    converted += 1
                    _log(message)
                else:
                    _log(f"ERR: {message}")
        except Exception as e:
            _log(f"ERR {img.name}: {e}")

    _log(f"Scan finished. scanned={scanned} converted={converted} deleted_orphans={deleted}")
    return ScanResult(scanned=scanned, converted=converted, deleted_orphans=deleted, mirrored_into_library=mirrored)


# ----------------------------
# UI dialogs
# ----------------------------

class WatchFoldersDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Watched Folders")
        self.resize(720, 420)

        v = QtWidgets.QVBoxLayout(self)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        v.addWidget(self.list, 1)

        h = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add…")
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        h.addWidget(self.btn_add)
        h.addWidget(self.btn_remove)
        h.addWidget(self.btn_clear)
        h.addStretch(1)

        self.btn_ok = QtWidgets.QPushButton("OK")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        h.addWidget(self.btn_ok)
        h.addWidget(self.btn_cancel)

        v.addLayout(h)

        for p in _load_watch_folders():
            self.list.addItem(p)

        self.btn_add.clicked.connect(self._add)
        self.btn_remove.clicked.connect(self._remove)
        self.btn_clear.clicked.connect(self.list.clear)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)

    def _add(self) -> None:
        dn = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder…", "")
        if dn:
            self.list.addItem(dn)

    def _remove(self) -> None:
        for it in self.list.selectedItems():
            r = self.list.row(it)
            self.list.takeItem(r)

    def paths(self) -> List[str]:
        out: List[str] = []
        for i in range(self.list.count()):
            out.append(self.list.item(i).text())
        return _unique_existing(out)


# ----------------------------
# Tray agent
# ----------------------------

class TrayAgent(QtWidgets.QSystemTrayIcon):
    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(get_app_icon(), parent=app)
        self.setToolTip("IconMaker — background agent")

        self._ipc = QtNetwork.QLocalServer(self)
        if not self._start_or_exit_if_running():
            # Another tray instance is already running; exit this process.
            QtCore.QTimer.singleShot(0, QtWidgets.QApplication.quit)
            return

        self.menu = QtWidgets.QMenu()

        act_open = self.menu.addAction("Open IconMaker")
        act_open.triggered.connect(self.open_gen1)

        self.menu.addSeparator()

        act_scan = self.menu.addAction("Scan Now")
        act_scan.triggered.connect(self._scan_now)

        self.act_pause = self.menu.addAction("Pause Watching")
        self.act_pause.setCheckable(True)
        self.act_pause.setChecked(_get_paused())
        self.act_pause.triggered.connect(self._toggle_pause)

        self.act_notify = self.menu.addAction("Notifications")
        self.act_notify.setCheckable(True)
        self.act_notify.setChecked(_get_notifications_enabled())
        self.act_notify.triggered.connect(lambda v: _set_notifications_enabled(bool(v)))

        self.menu.addSeparator()

        act_watch = self.menu.addAction("Watched Folders…")
        act_watch.triggered.connect(self._edit_watch_folders)

        self.menu.addSeparator()

        act_exit = self.menu.addAction("Exit")
        act_exit.triggered.connect(QtWidgets.QApplication.quit)

        self.setContextMenu(self.menu)
        self.activated.connect(self._on_click)

        # Debounced scan (directoryChanged may fire multiple times)
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(650)
        self._debounce.timeout.connect(self._scan_now)

        self.watcher = QtCore.QFileSystemWatcher(self)

        self._periodic = QtCore.QTimer(self)
        self._periodic.setInterval(SCAN_INTERVAL_MS)
        self._periodic.timeout.connect(self._scan_now)

        if not _get_paused():
            self._attach_watch()
            self._periodic.start()

        # Startup catch-up scan
        QtCore.QTimer.singleShot(700, self._scan_now)

        self.show()

    # ----- IPC / single instance -----

    def _start_or_exit_if_running(self) -> bool:
        """
        Returns True if we successfully became the tray instance.
        Returns False if another instance is already running.
        """
        # If a server already exists, attempt to connect; if connection works, exit.
        probe = QtNetwork.QLocalSocket(self)
        probe.connectToServer(TRAY_IPC_NAME)
        if probe.waitForConnected(120):
            try:
                probe.disconnectFromServer()
            except Exception:
                pass
            return False

        # Remove stale server (crash leftovers)
        try:
            QtNetwork.QLocalServer.removeServer(TRAY_IPC_NAME)
        except Exception:
            pass

        try:
            self._ipc.newConnection.connect(self._on_ipc_connection)
            ok = self._ipc.listen(TRAY_IPC_NAME)
            return bool(ok)
        except Exception:
            return True  # if IPC fails, still run tray

    def _on_ipc_connection(self) -> None:
        try:
            sock = self._ipc.nextPendingConnection()
            if sock:
                sock.disconnectFromServer()
                sock.deleteLater()
        except Exception:
            pass

    # ----- Watching -----

    def _watch_paths(self) -> List[str]:
        paths: List[str] = []
        if ICON_IMAGES_DIR.exists():
            paths.append(str(ICON_IMAGES_DIR))

        for p in _load_watch_folders():
            if Path(p).exists():
                paths.append(p)

        return _unique_existing(paths)

    def _attach_watch(self) -> None:
        # Clear old
        try:
            old = self.watcher.directories()
            if old:
                self.watcher.removePaths(old)
        except Exception:
            pass

        # Disconnect safely (Qt throws if not connected)
        try:
            self.watcher.directoryChanged.disconnect()
        except Exception:
            pass

        paths = self._watch_paths()
        if paths:
            try:
                self.watcher.addPaths(paths)
            except Exception as e:
                _log(f"Watcher addPaths ERR: {e}")

        # Reconnect
        self.watcher.directoryChanged.connect(lambda *_: self._debounce.start())

        _log(f"Watcher attached: {len(paths)} path(s).")

    # ----- Scanning -----

    def _scan_now(self) -> None:
        if _get_paused():
            return

        res = scan_and_convert(autocrop=False, padding_mode="balanced")

        # If watch folders changed on disk (deleted/moved), re-attach watch list periodically
        # (QFileSystemWatcher can silently drop invalid paths).
        self._attach_watch()

        if _get_notifications_enabled() and (res.converted or res.deleted_orphans):
            self.showMessage(
                "IconMaker",
                f"Converted: {res.converted}   |   Deleted orphans: {res.deleted_orphans}",
                QtWidgets.QSystemTrayIcon.Information,
                2500,
            )

    def _toggle_pause(self, checked: bool) -> None:
        _set_paused(bool(checked))
        if checked:
            try:
                old = self.watcher.directories()
                if old:
                    self.watcher.removePaths(old)
            except Exception:
                pass
            self._periodic.stop()
            _log("Watching paused.")
            if _get_notifications_enabled():
                self.showMessage("IconMaker", "Watching paused.", QtWidgets.QSystemTrayIcon.Information, 1500)
        else:
            self._attach_watch()
            self._periodic.start()
            _log("Watching resumed.")
            if _get_notifications_enabled():
                self.showMessage("IconMaker", "Watching resumed. Scanning…", QtWidgets.QSystemTrayIcon.Information, 1500)
            QtCore.QTimer.singleShot(200, self._scan_now)

    def _edit_watch_folders(self) -> None:
        dlg = WatchFoldersDialog()
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            _save_watch_folders(dlg.paths())
            _log("Watch folders updated.")
            if not _get_paused():
                self._attach_watch()

    # ----- UI events -----

    def _on_click(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            m = self.contextMenu()
            if m:
                m.popup(QtGui.QCursor.pos())

    # ----- Launch UI -----

    def _run_detached(self, argv: List[str]) -> bool:
        try:
            if sys.platform.startswith("win"):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
                subprocess.Popen(argv, close_fds=True, creationflags=creationflags)
            else:
                subprocess.Popen(argv, close_fds=True)
            return True
        except Exception as e:
            _log(f"Launch ERR: {argv} -> {e}")
            return False

    def open_gen1(self) -> None:
        base = _exe_dir()

        # Frozen: try known exe names
        if getattr(sys, "frozen", False):
            for exe in (base / "Gen1.exe", base / "IconMaker.exe", base / "IMUI.exe"):
                if exe.exists():
                    if self._run_detached([str(exe)]):
                        return

        # Source: prefer venv pythonw/python
        scripts = _venv_scripts_dir()
        pyw = scripts / "pythonw.exe"
        py = scripts / "python.exe"
        python = str(pyw if pyw.exists() else (py if py.exists() else Path(sys.executable)))

        for candidate in (base / "Gen1.py", base.parent / "Gen1.py"):
            if candidate.exists():
                if self._run_detached([python, str(candidate)]):
                    return


# ----------------------------
# main
# ----------------------------

def main() -> None:
    _pre_app_setup()

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(get_app_icon())

    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        _log("System tray not available. Exiting.")
        return

    _ = TrayAgent(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
