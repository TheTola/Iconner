#!/usr/bin/env python3
r"""
Gen1.py — IconMaker Main UI (Dark Neon) — UI PRESERVED + Redesign Applied

You said the UI layout/visual structure MUST stay like your current Gen1.
So this version keeps your existing UI structure (Hero + Cards + Scroll + Queue + Log)
and applies ONLY the functional redesign changes:

✅ Scan button removed from UI (Scan becomes internal-only)
✅ Run = COPY into Icon Images/ (canonical) → convert from the COPIED files → post-run maintenance scan
✅ Output is canonical ICONS_DIR (the Output card remains, but is locked to ICONS_DIR)
✅ Maintenance scan runs automatically:
   - startup
   - shutdown
   - file-system changes inside Icon Images/ (debounced)

✅ Default sizes updated to:
   16,24,32,48,64,128,256,512,1024
"""

from __future__ import annotations

import os
import sys
import time
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

import self
from PySide6 import QtCore, QtGui, QtWidgets, QtNetwork

import Gen2 as eng
from Gen4 import (
    get_app_icon,
    get_title_pixmap,
    ICON_IMAGES_DIR,
    ICONS_DIR,
)

APP_ORG = "InfiniWorks"
APP_NAME = "IconMaker"

SAGE_URL = "https://chatgpt.com/g/g-68e8c5f35ff0819195a81c501942a072-sage-of-iconer"
DEFAULT_OUTPUT_DIR = str(ICONS_DIR)

# --- Sage button image (PNG) ---
SAGE_BUTTON_IMAGE_PATH = r"C:\Users\Oluwatola Ayedun\Desktop\IconMaker\assets\IcoSage.png"

# Controls the Sage button size:
SAGE_BTN_SIZE = 140

# ---------------- Tray IPC (optional) ----------------
TRAY_IPC_NAME = "IconMaker_TrayIPC"

def _count_files(root: Path) -> int:
    n = 0
    for _dirpath, _dirnames, filenames in os.walk(root):
        n += len(filenames)
    return n


class LibraryRelocateWorker(QtCore.QObject):
    """
    Copies an entire folder tree from src_root -> dst_root.
    Emits progress without freezing UI.
    """
    progress = QtCore.Signal(int, int, str)   # done, total, current_path
    finished = QtCore.Signal(bool, str)       # ok, message

    def __init__(self, src_root: Path, dst_root: Path):
        super().__init__()
        self.src_root = Path(src_root)
        self.dst_root = Path(dst_root)
        self._cancel = False

    @QtCore.Slot()
    def run(self) -> None:
        try:
            if not self.src_root.exists():
                self.finished.emit(False, f"Source root does not exist: {self.src_root}")
                return

            self.dst_root.mkdir(parents=True, exist_ok=True)

            total = _count_files(self.src_root)
            done = 0

            for dirpath, dirnames, filenames in os.walk(self.src_root):
                if self._cancel:
                    self.finished.emit(False, "Cancelled.")
                    return

                rel = Path(dirpath).relative_to(self.src_root)
                target_dir = self.dst_root / rel
                target_dir.mkdir(parents=True, exist_ok=True)

                for fn in filenames:
                    if self._cancel:
                        self.finished.emit(False, "Cancelled.")
                        return

                    src_file = Path(dirpath) / fn
                    dst_file = target_dir / fn

                    # Overwrite if exists (we're relocating the canonical library)
                    try:
                        shutil.copy2(src_file, dst_file)
                    except Exception as e:
                        self.finished.emit(False, f"Copy failed: {src_file} -> {dst_file} ({e})")
                        return

                    done += 1
                    self.progress.emit(done, total, str(src_file))

            self.finished.emit(True, "Copy complete.")
        except Exception as e:
            self.finished.emit(False, str(e))

    def cancel(self) -> None:
        self._cancel = True

def _pre_app_setup() -> None:
    """Windows AppUserModelID (helps taskbar grouping and icon association)."""
    if sys.platform.startswith("win"):
        try:
            import ctypes  # local import intentional
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ORG}.{APP_NAME}")
        except Exception:
            pass


def _is_tray_running(timeout_ms: int = 250) -> bool:
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(TRAY_IPC_NAME)
    if not sock.waitForConnected(timeout_ms):
        sock.abort()
        return False
    sock.abort()
    return True


def _base_dir_for_launch() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _start_tray_process() -> bool:
    base = _base_dir_for_launch()

    for exe in (base / "Gen3.exe", base / "IconMaker_Tray.exe", base / "IMTray.exe"):
        if exe.exists():
            try:
                os.startfile(str(exe))  # type: ignore[attr-defined]
                return True
            except Exception:
                pass

    for guess in (base / "Gen3.py", base.parent / "Gen3.py"):
        if guess.exists():
            try:
                if sys.platform.startswith("win"):
                    subprocess.Popen(["pythonw.exe", str(guess)], close_fds=True)
                else:
                    subprocess.Popen([sys.executable, str(guess)], close_fds=True)
                return True
            except Exception:
                continue

    return False


def ensure_tray_running() -> None:
    if _is_tray_running():
        return
    if not _start_tray_process():
        return
    for _ in range(30):
        if _is_tray_running():
            return
        time.sleep(0.1)


# ---------------- UI helpers ----------------
def _repolish(widget: QtWidgets.QWidget) -> None:
    """Re-apply stylesheet after dynamic property changes."""
    try:
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
    except Exception:
        widget.update()


def _open_path(path: str) -> None:
    """Open a folder/file with OS default behavior."""
    p = Path(path)
    if not p.exists():
        return
    if sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))


def _is_image_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}


def _gather_images(input_path: Path, recursive: bool) -> list[Path]:
    """
    Turn a user input (file/folder) into a concrete list of image files.
    """
    if input_path.is_file():
        return [input_path] if _is_image_file(input_path) else []
    if input_path.is_dir():
        return list(eng.find_images(input_path, recursive=recursive))
    return []





class CardFrame(QtWidgets.QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        if title:
            t = QtWidgets.QLabel(title)
            t.setObjectName("CardTitle")
            lay.addWidget(t)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

    def body_layout(self) -> QtWidgets.QVBoxLayout:
        return self.layout()  # type: ignore[return-value]




class DropLineEdit(QtWidgets.QLineEdit):
    pathDropped = QtCore.Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)
        self.setProperty("dropActive", False)

    def _set_drag(self, on: bool) -> None:
        self.setProperty("dropActive", on)
        _repolish(self)

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            self._set_drag(True)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        self._set_drag(False)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        self._set_drag(False)
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                self.pathDropped.emit(u.toLocalFile())
                break
            return
        super().dropEvent(e)


class NeonCTAButton(QtWidgets.QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("NeonCTA")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._fx = QtWidgets.QGraphicsDropShadowEffect(self)
        self._fx.setOffset(0, 0)
        self._fx.setBlurRadius(44)
        self._fx.setColor(QtGui.QColor(0, 220, 255, 180))
        self.setGraphicsEffect(self._fx)

        self._pulse = QtCore.QPropertyAnimation(self, b"glow")
        self._pulse.setStartValue(26)
        self._pulse.setEndValue(64)
        self._pulse.setDuration(1200)
        self._pulse.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.start()

    def getGlow(self) -> int:
        return int(self._fx.blurRadius())

    def setGlow(self, v: int) -> None:
        self._fx.setBlurRadius(v)
        self._fx.setColor(QtGui.QColor(0, 220, 255, 150 if v < 44 else 220))

    glow = QtCore.Property(int, fget=getGlow, fset=setGlow)


class MetallicRedButton(QtWidgets.QPushButton):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("MetalRed")
        self.setCursor(QtCore.Qt.PointingHandCursor)

        fx = QtWidgets.QGraphicsDropShadowEffect(self)
        fx.setOffset(0, 0)
        fx.setBlurRadius(54)
        fx.setColor(QtGui.QColor(255, 40, 40, 200))
        self.setGraphicsEffect(fx)

        self._pulse = QtCore.QPropertyAnimation(self, b"hot")
        self._pulse.setStartValue(30)
        self._pulse.setEndValue(74)
        self._pulse.setDuration(1000)
        self._pulse.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._pulse.setLoopCount(-1)
        self._pulse.start()

    def getHot(self) -> int:
        fx = self.graphicsEffect()
        if isinstance(fx, QtWidgets.QGraphicsDropShadowEffect):
            return int(fx.blurRadius())
        return 0

    def setHot(self, v: int) -> None:
        fx = self.graphicsEffect()
        if isinstance(fx, QtWidgets.QGraphicsDropShadowEffect):
            fx.setBlurRadius(v)

    hot = QtCore.Property(int, fget=getHot, fset=setHot)


class SegmentedMode(QtWidgets.QWidget):
    modeChanged = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SegMode")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.btn_file = QtWidgets.QPushButton("File")
        self.btn_folder = QtWidgets.QPushButton("Folder")
        for b in (self.btn_file, self.btn_folder):
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)

        self.group = QtWidgets.QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.addButton(self.btn_file)
        self.group.addButton(self.btn_folder)
        lay.addWidget(self.btn_file)
        lay.addWidget(self.btn_folder)

        self.btn_file.setChecked(True)
        self.group.buttonClicked.connect(self._emit)  # type: ignore[arg-type]

    def _emit(self) -> None:
        self.modeChanged.emit("folder" if self.btn_folder.isChecked() else "file")

    def set_mode(self, mode: str) -> None:
        if mode == "folder":
            self.btn_folder.setChecked(True)
        else:
            self.btn_file.setChecked(True)
        self._emit()


class NeonRippleIconButton(QtWidgets.QPushButton):
    """
    Icon-only button:
    - icon fills the entire button space (no inset frame look)
    - animated hover glow
    - neon ripple on click
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SageIconBtn")
        self.setCursor(QtCore.Qt.PointingHandCursor)

        self.setText("")
        self.setCheckable(False)
        self.setFlat(True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setAutoDefault(False)
        self.setDefault(False)

        self.setStyleSheet("padding:0px; margin:0px; border:none; background: transparent;")

        self._glow_blur = 18
        self._glow_alpha = 120

        self._ripple_active = False
        self._ripple_center = QtCore.QPointF(0, 0)
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0

        self._glow_fx = QtWidgets.QGraphicsDropShadowEffect(self)
        self._glow_fx.setOffset(0, 0)
        self._glow_fx.setBlurRadius(self._glow_blur)
        self._glow_fx.setColor(QtGui.QColor(0, 220, 255, self._glow_alpha))
        self.setGraphicsEffect(self._glow_fx)

        self._glow_anim = QtCore.QPropertyAnimation(self, b"glowBlur", self)
        self._glow_anim.setDuration(180)
        self._glow_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._alpha_anim = QtCore.QPropertyAnimation(self, b"glowAlpha", self)
        self._alpha_anim.setDuration(180)
        self._alpha_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._rip_r_anim = QtCore.QPropertyAnimation(self, b"rippleRadius", self)
        self._rip_r_anim.setDuration(420)
        self._rip_r_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._rip_o_anim = QtCore.QPropertyAnimation(self, b"rippleOpacity", self)
        self._rip_o_anim.setDuration(420)
        self._rip_o_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        self._rip_group = QtCore.QParallelAnimationGroup(self)
        self._rip_group.addAnimation(self._rip_r_anim)
        self._rip_group.addAnimation(self._rip_o_anim)
        self._rip_group.finished.connect(self._ripple_end)  # type: ignore[arg-type]

    def _get_glow_blur(self) -> int:
        return int(self._glow_blur)

    def _set_glow_blur(self, v: int) -> None:
        self._glow_blur = int(v)
        self._glow_fx.setBlurRadius(int(v))

    glowBlur = QtCore.Property(int, fget=_get_glow_blur, fset=_set_glow_blur)

    def _get_glow_alpha(self) -> int:
        return int(self._glow_alpha)

    def _set_glow_alpha(self, v: int) -> None:
        self._glow_alpha = int(v)
        self._glow_fx.setColor(QtGui.QColor(0, 220, 255, max(0, min(255, int(v)))))

    glowAlpha = QtCore.Property(int, fget=_get_glow_alpha, fset=_set_glow_alpha)

    def _get_ripple_radius(self) -> float:
        return float(self._ripple_radius)

    def _set_ripple_radius(self, v: float) -> None:
        self._ripple_radius = float(v)
        self.update()

    rippleRadius = QtCore.Property(float, fget=_get_ripple_radius, fset=_set_ripple_radius)

    def _get_ripple_opacity(self) -> float:
        return float(self._ripple_opacity)

    def _set_ripple_opacity(self, v: float) -> None:
        self._ripple_opacity = float(v)
        self.update()

    rippleOpacity = QtCore.Property(float, fget=_get_ripple_opacity, fset=_set_ripple_opacity)

    def set_icon_from_png(self, png_path: str) -> None:
        p = Path(png_path)
        if not p.exists():
            return
        pm = QtGui.QPixmap(str(p))
        if pm.isNull():
            return

        btn = int(SAGE_BTN_SIZE)
        self.setFixedSize(btn, btn)
        self.setIcon(QtGui.QIcon(pm))
        self.setIconSize(QtCore.QSize(btn, btn))
        self.setMask(QtGui.QRegion(QtCore.QRect(0, 0, btn, btn), QtGui.QRegion.Rectangle))

    def enterEvent(self, e: QtCore.QEvent) -> None:
        self._glow_anim.stop()
        self._alpha_anim.stop()

        self._glow_anim.setStartValue(self._get_glow_blur())
        self._glow_anim.setEndValue(54)

        self._alpha_anim.setStartValue(self._get_glow_alpha())
        self._alpha_anim.setEndValue(235)

        self._glow_anim.start()
        self._alpha_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e: QtCore.QEvent) -> None:
        self._glow_anim.stop()
        self._alpha_anim.stop()

        self._glow_anim.setStartValue(self._get_glow_blur())
        self._glow_anim.setEndValue(18)

        self._alpha_anim.setStartValue(self._get_glow_alpha())
        self._alpha_anim.setEndValue(120)

        self._glow_anim.start()
        self._alpha_anim.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == QtCore.Qt.LeftButton:
            self._ripple_active = True
            self._ripple_center = e.position()

            max_r = (self.width() ** 2 + self.height() ** 2) ** 0.5 * 0.75

            self._rip_group.stop()
            self._rip_r_anim.setStartValue(0.0)
            self._rip_r_anim.setEndValue(float(max_r))

            self._rip_o_anim.setStartValue(0.42)
            self._rip_o_anim.setEndValue(0.0)

            self._rip_group.start()
        super().mousePressEvent(e)

    def _ripple_end(self) -> None:
        self._ripple_active = False
        self._ripple_radius = 0.0
        self._ripple_opacity = 0.0
        self.update()

    def paintEvent(self, e: QtGui.QPaintEvent) -> None:
        super().paintEvent(e)

        if not self._ripple_active and self._ripple_opacity <= 0.001:
            return

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        col = QtGui.QColor(0, 220, 255)
        col.setAlphaF(max(0.0, min(1.0, self._ripple_opacity)))

        p.setBrush(QtGui.QBrush(col))
        p.setPen(QtCore.Qt.NoPen)

        cx, cy = self._ripple_center.x(), self._ripple_center.y()
        r = self._ripple_radius
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        p.end()


@dataclass(slots=True)
class LogLine:
    text: str
    level: str = "INFO"  # INFO/WARN/ERR

def preset_sizes(preset: str) -> list[int]:
    """
    Presets:
      16–1024, 16–512, 16–256, …, 16–16
    Returns a deterministic list of common icon sizes.
    """
    preset = (preset or "").strip()

    # Extract the max after the dash, default to 256 if parsing fails
    try:
        max_size = int(preset.split("–", 1)[1])
    except Exception:
        max_size = 256

    # Standard icon ladder (you accepted these endpoints)
    ladder = [16, 24, 32, 48, 64, 96, 128, 256, 512, 1024]

    # Keep only <= max_size, but always include 16
    out = [s for s in ladder if s <= max_size]
    if 16 not in out:
        out.insert(0, 16)
    return out

def choose_library_root(parent) -> Path | None:
    p = QtWidgets.QFileDialog.getExistingDirectory(
        parent,
        "Choose IconMaker Library Location",
        QtCore.QDir.homePath(),
    )
    return Path(p) if p else None

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()

        # ---------------- Settings (single instance) ----------------
        self._settings = QtCore.QSettings(APP_ORG, APP_NAME)

        # ---------------- Library root (one-time selection / restore) ----------------
        root_str = str(self._settings.value("library_root", "") or "").strip()
        root = Path(root_str) if root_str else None

        if not root or not root.exists():
            chosen = choose_library_root(self)
            if not chosen:
                QtWidgets.QMessageBox.critical(
                    self,
                    "IconMaker",
                    "A library folder is required to continue.",
                )
                sys.exit(1)
            self._settings.setValue("library_root", str(chosen))
            root = chosen

        # Apply canonical paths BEFORE watchers/UI
        self._set_library_paths(root)

        # --- maintenance / watcher state ---
        self._maint_busy = False
        self._maint_pending_reason = None

        self._fs_watcher = QtCore.QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_icon_images_fs_event)
        self._fs_watcher.fileChanged.connect(self._on_icon_images_fs_event)

        self._fs_debounce = QtCore.QTimer(self)
        self._fs_debounce.setSingleShot(True)
        self._fs_debounce.setInterval(400)
        self._fs_debounce.timeout.connect(lambda: self._maintenance_request("fs-change"))

        self._app_icon = get_app_icon()
        self.setWindowIcon(self._app_icon)

        self.setWindowTitle("IconMaker")
        self.setMinimumSize(1180, 760)
        self.setAcceptDrops(True)


        self._log_buffer: list[LogLine] = []
        self._log_flush_timer = QtCore.QTimer(self)
        self._log_flush_timer.setInterval(80)
        self._log_flush_timer.timeout.connect(self._flush_log)
        self._log_flush_timer.start()

        self._cancel_requested = False

        # Build UI first
        self._build_ui()
        self._apply_theme()
        self._wire()
        # ---------------- Library root (one-time selection) ----------------
        root = self._settings.value("library_root", "", str)

        if not root or not Path(root).exists():
            chosen = choose_library_root(self)
            if not chosen:
                QtWidgets.QMessageBox.critical(
                    self,
                    "IconMaker",
                    "A library folder is required to continue.",
                )
                sys.exit(1)

            self._settings.setValue("library_root", str(chosen))
            root = str(chosen)

        self.LIBRARY_ROOT = Path(root)

        # Canonical paths
        global ICON_IMAGES_DIR, ICONS_DIR
        ICON_IMAGES_DIR = self.LIBRARY_ROOT / "Icon Images"
        ICONS_DIR = ICON_IMAGES_DIR / "Icons"

        ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        ICONS_DIR.mkdir(parents=True, exist_ok=True)

        # ---------------- Restore state ----------------
        last_input = self._settings.value("last_input", "", str)
        if last_input:
            self.edit_input.setText(last_input)
            p = Path(last_input)
            self.mode_seg.set_mode("folder" if p.is_dir() else "file")

        self.chk_overwrite.setChecked(
            self._settings.value("last_overwrite", True, bool)
        )
        pad = self._settings.value("last_padding", "balanced", str)
        if self.cmb_padding.findText(pad) >= 0:
            self.cmb_padding.setCurrentText(pad)

        # ---------------- Persist on change ----------------
        self.edit_input.textChanged.connect(
            lambda v: self._settings.setValue("last_input", v)
        )
        self.chk_recursive.toggled.connect(
            lambda b: self._settings.setValue("last_recursive", b)
        )
        self.chk_overwrite.toggled.connect(
            lambda b: self._settings.setValue("last_overwrite", b)
        )
        self.cmb_padding.currentTextChanged.connect(
            lambda t: self._settings.setValue("last_padding", t)
        )
        self.mode_seg.modeChanged.connect(
            lambda m: self._settings.setValue("last_mode", m)
        )

        ensure_tray_running()
        self._log("Ready.")

        self._lock_output_to_canonical()
        self._arm_icon_images_watcher()

        def _set_library_paths(self, new_root: Path) -> None:
            """
            Update canonical library paths after relocation and refresh UI/watcher.
            """
            new_root = Path(new_root)

            global ICON_IMAGES_DIR, ICONS_DIR
            ICON_IMAGES_DIR = new_root / "Icon Images"
            ICONS_DIR = ICON_IMAGES_DIR / "Icons"
            ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            ICONS_DIR.mkdir(parents=True, exist_ok=True)

            # Update UI label if present
            if hasattr(self, "lbl_outdir"):
                self.lbl_outdir.setText(str(ICONS_DIR))

            # Re-arm watcher if you have one
            if hasattr(self, "_fs_watcher") and hasattr(self, "_arm_icon_images_watcher"):
                try:
                    self._arm_icon_images_watcher()
                except Exception:
                    pass

        def _change_library_location(self) -> None:
            """
            Copy current library root to a new location, then (optionally) delete old root.
            Updates QSettings on success.
            """
            # Current root is the parent of "Icon Images"
            try:
                old_root = Path(ICON_IMAGES_DIR).parent
            except Exception:
                old_root = None

            start_dir = str(old_root) if old_root and old_root.exists() else QtCore.QDir.homePath()
            picked = QtWidgets.QFileDialog.getExistingDirectory(
                self,
                "Choose NEW IconMaker Library Location",
                start_dir,
            )
            if not picked:
                return

            new_root = Path(picked).resolve()
            if old_root and new_root == old_root.resolve():
                self._log("Library location unchanged.", "WARN")
                return

            # Destination root must be empty or you accept merge/overwrite
            # We will create: <new_root>/Icon Images/...
            dst_icon_images = new_root / "Icon Images"
            if dst_icon_images.exists():
                r = QtWidgets.QMessageBox.question(
                    self,
                    "IconMaker",
                    "This location already contains an 'Icon Images' folder.\n\n"
                    "Proceed and OVERWRITE files where needed?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No,
                )
                if r != QtWidgets.QMessageBox.Yes:
                    return

            # Prepare background copy worker
            src = old_root if old_root else Path(ICON_IMAGES_DIR).parent
            dst = new_root

            dlg = QtWidgets.QProgressDialog("Relocating library…", "Cancel", 0, 100, self)
            dlg.setWindowTitle("IconMaker")
            dlg.setWindowModality(QtCore.Qt.WindowModal)
            dlg.setAutoClose(False)
            dlg.setAutoReset(False)
            dlg.show()

            thread = QtCore.QThread(self)
            worker = LibraryRelocateWorker(src, dst)
            worker.moveToThread(thread)

            def on_progress(done: int, total: int, current: str) -> None:
                if total <= 0:
                    dlg.setValue(0)
                    return
                pct = int(done * 100 / total)
                dlg.setValue(max(0, min(100, pct)))
                dlg.setLabelText(f"Copying… ({done}/{total})\n{current}")

            def on_cancel() -> None:
                worker.cancel()

            def on_finished(ok: bool, msg: str) -> None:
                thread.quit()
                thread.wait(1500)
                dlg.close()

                if not ok:
                    self._log(f"Library relocate failed: {msg}", "ERR")
                    QtWidgets.QMessageBox.critical(self, "IconMaker", f"Relocate failed:\n{msg}")
                    return

                # Update settings + globals
                self._settings.setValue("library_root", str(new_root))
                self._set_library_paths(new_root)

                self._log(f"Library relocated to: {new_root}")

                # Offer to delete old root (turn copy into MOVE)
                r = QtWidgets.QMessageBox.question(
                    self,
                    "IconMaker",
                    "Copy complete.\n\nDelete the OLD library folder to finish the move?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.Yes,
                )
                if r == QtWidgets.QMessageBox.Yes and old_root and old_root.exists():
                    try:
                        shutil.rmtree(old_root)
                        self._log(f"Old library deleted: {old_root}")
                    except Exception as e:
                        self._log(f"WARN: Could not delete old library ({e})", "WARN")
                        QtWidgets.QMessageBox.warning(
                            self,
                            "IconMaker",
                            f"New library is active, but old folder could not be deleted:\n{e}",
                        )

            worker.progress.connect(on_progress)  # type: ignore[arg-type]
            worker.finished.connect(on_finished)  # type: ignore[arg-type]
            dlg.canceled.connect(on_cancel)  # type: ignore[arg-type]

            thread.started.connect(worker.run)  # type: ignore[arg-type]
            thread.start()

    # -------- UI build --------
    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        # ---------------- HERO (top) ----------------
        hero = QtWidgets.QFrame()
        hero.setObjectName("Hero")
        h = QtWidgets.QHBoxLayout(hero)
        h.setContentsMargins(18, 16, 18, 16)
        h.setSpacing(14)

        self.mark = QtWidgets.QLabel()
        self.mark.setObjectName("AppMark")
        self.mark.setFixedSize(44, 44)

        pm = get_title_pixmap()
        if not pm.isNull():
            self.mark.setPixmap(pm.scaled(38, 38, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            self.mark.setAlignment(QtCore.Qt.AlignCenter)

        title_wrap = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("IconMaker")
        title.setObjectName("HeroTitle")
        subtitle = QtWidgets.QLabel(" ")
        subtitle.setObjectName("HeroSub")
        title_wrap.addWidget(title)
        title_wrap.addWidget(subtitle)

        self.btn_sage = NeonRippleIconButton()
        self.btn_sage.setToolTip(SAGE_URL)
        self.btn_sage.set_icon_from_png(SAGE_BUTTON_IMAGE_PATH)

        h.addWidget(self.mark)
        h.addLayout(title_wrap, 1)
        h.addWidget(self.btn_sage, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        outer.addWidget(hero)

        self.btn_sage.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(SAGE_URL)))  # type: ignore[arg-type]

        # ---------------- TOP PANEL (Run/Cancel/Progress) ----------------
        top_controls = CardFrame("")
        top_controls.setObjectName("TopControlsCard")
        outer.addWidget(top_controls)

        tcl = QtWidgets.QVBoxLayout()
        tcl.setContentsMargins(0, 0, 0, 0)
        tcl.setSpacing(10)
        top_controls.body_layout().addLayout(tcl)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        # Scan removed
        self.btn_run = NeonCTAButton("Run")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_cancel.setObjectName("CancelBtn")
        self.btn_cancel.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_cancel.setEnabled(False)

        for b in (self.btn_run, self.btn_cancel):
            b.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            b.setMinimumWidth(110)
            b.setMaximumWidth(140)

        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addStretch(1)

        tcl.addLayout(btn_row)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)

        self.status_line = QtWidgets.QLabel("Ready.")
        self.status_line.setObjectName("StatusLine")

        tcl.addWidget(self.bar)
        tcl.addWidget(self.status_line)

        # ---------------- Body scroll (everything else) ----------------
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        body = QtWidgets.QWidget()
        scroll.setWidget(body)

        main = QtWidgets.QHBoxLayout(body)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(14)

        left_wrap = QtWidgets.QWidget()
        right_wrap = QtWidgets.QWidget()
        left_wrap.setMinimumWidth(620)

        left = QtWidgets.QVBoxLayout(left_wrap)
        left.setSpacing(10)
        right = QtWidgets.QVBoxLayout(right_wrap)
        right.setSpacing(10)

        main.addWidget(left_wrap, 7)
        main.addWidget(right_wrap, 8)

        # Drop Zone
        self.drop_zone = CardFrame("Drop Zone")
        self.drop_zone.setObjectName("DropZoneCard")
        dz = QtWidgets.QLabel("Drag files or folders onto the window.")
        dz.setObjectName("DropZoneText")
        self.drop_zone.body_layout().addWidget(dz)
        left.addWidget(self.drop_zone)

        # Source
        source = CardFrame("Source")
        left.addWidget(source)
        sg = QtWidgets.QGridLayout()
        sg.setHorizontalSpacing(10)
        sg.setVerticalSpacing(10)
        source.body_layout().addLayout(sg)

        self.mode_seg = SegmentedMode()
        sg.addWidget(self.mode_seg, 0, 0, 1, 3)

        self.edit_input = DropLineEdit()
        self.edit_input.setPlaceholderText("Drop a file/folder here… or click Browse.")
        self.btn_browse_input = QtWidgets.QPushButton("Browse…")
        self.btn_browse_input.setCursor(QtCore.Qt.PointingHandCursor)
        sg.addWidget(self.edit_input, 1, 0, 1, 2)
        sg.addWidget(self.btn_browse_input, 1, 2)

        self.chk_recursive = QtWidgets.QCheckBox("Recursive (subfolders)")
        sg.addWidget(self.chk_recursive, 2, 0, 1, 2)

        # Output (fixed)
        out = CardFrame("Output")
        left.addWidget(out)

        og = QtWidgets.QGridLayout()
        og.setHorizontalSpacing(10)
        og.setVerticalSpacing(10)
        out.body_layout().addLayout(og)

        # Fixed output location (no user-edit, no browse)
        self.lbl_outdir = QtWidgets.QLabel(str(ICONS_DIR))
        self.lbl_outdir.setObjectName("FixedOutPath")
        self.lbl_outdir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.btn_open_my_icons = QtWidgets.QPushButton("Open My Icons")
        self.btn_open_my_icons.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_open_images = QtWidgets.QPushButton("Open Icon Images")
        self.btn_open_images.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_change_library = QtWidgets.QPushButton("Change Library Location…")
        self.btn_change_library.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_open_my_icons.clicked.connect(lambda: _open_path(str(ICONS_DIR)))  # type: ignore[arg-type]
        self.btn_open_images.clicked.connect(lambda: _open_path(str(ICON_IMAGES_DIR)))  # type: ignore[arg-type]
        self.btn_change_library.clicked.connect(self._change_library_location)  # type: ignore[arg-type]

        og.addWidget(QtWidgets.QLabel("Icon Output"), 0, 0)
        og.addWidget(self.lbl_outdir, 0, 1, 1, 2)
        og.addWidget(self.btn_open_my_icons, 1, 0, 1, 3)
        og.addWidget(self.btn_open_images, 2, 0, 1, 3)
        og.addWidget(self.btn_change_library, 3, 0, 1, 3)

        # Options
        opt = CardFrame("Icon Quality")
        left.addWidget(opt)
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(10)
        opt.body_layout().addLayout(g)

        self.cmb_quality = QtWidgets.QComboBox()
        self.cmb_quality.setCursor(QtCore.Qt.PointingHandCursor)

        # Presets you requested
        presets = [
            "16–1024",
            "16–512",
            "16–256",
            "16–128",
            "16–64",
            "16–48",
            "16–32",
            "16–24",
            "16–16",
        ]
        self.cmb_quality.addItems(presets)
        self.cmb_quality.setCurrentText("16–1024")

        self.chk_overwrite = QtWidgets.QCheckBox("Overwrite Mode")
        self.chk_overwrite.setChecked(True)

        self.cmb_padding = QtWidgets.QComboBox()
        self.cmb_padding.addItems(list(eng.PADDING_PRESETS.keys()))
        self.cmb_padding.setCurrentText("balanced")

        g.addWidget(QtWidgets.QLabel("Quality Preset"), 0, 0)
        g.addWidget(self.cmb_quality, 0, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("Padding"), 1, 0)
        g.addWidget(self.cmb_padding, 1, 1)
        g.addWidget(self.chk_overwrite, 1, 2, 1, 2)

        left.addStretch(1)

        # ---------- Right column: Queue + Log ----------
        qcard = CardFrame("Queue")
        right.addWidget(qcard)
        qv = QtWidgets.QVBoxLayout()
        qcard.body_layout().addLayout(qv)

        self.queue = QtWidgets.QTableWidget(0, 3)
        self.queue.setHorizontalHeaderLabels(["Status", "Name", "Path"])
        self.queue.horizontalHeader().setStretchLastSection(True)
        self.queue.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.queue.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.queue.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.queue.verticalHeader().setVisible(False)

        self.queue.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.queue.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.queue.horizontalHeader().setMinimumSectionSize(90)

        qv.addWidget(self.queue, 1)

        lcard = CardFrame("Log")
        right.addWidget(lcard, 1)

        fbar = QtWidgets.QHBoxLayout()
        self.cmb_filter = QtWidgets.QComboBox()
        self.cmb_filter.addItems(["All", "INFO", "WARN", "ERR"])
        self.btn_clear_log = QtWidgets.QPushButton("Clear Log")
        self.btn_clear_log.setCursor(QtCore.Qt.PointingHandCursor)
        fbar.addWidget(QtWidgets.QLabel("Filter"))
        fbar.addWidget(self.cmb_filter)
        fbar.addStretch(1)
        fbar.addWidget(self.btn_clear_log)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)

        lcard.body_layout().addLayout(fbar)
        lcard.body_layout().addWidget(self.log, 1)

    def _apply_theme(self) -> None:
        f = self.font()
        f.setFamily("Segoe UI Variable" if sys.platform.startswith("win") else "Segoe UI")
        f.setPointSize(11)
        self.setFont(f)

        self.setStyleSheet(r"""
        QMainWindow {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #060812, stop:0.45 #070B18, stop:1 #0B0620);
        }
        #Hero {
            border-radius: 18px;
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #081026, stop:0.55 #0B1240, stop:1 #24063D);
            border: 1px solid rgba(255,255,255,0.06);
        }
        #AppMark {
            border-radius: 22px;
            background: qradialgradient(cx:0.28, cy:0.25, radius:1.2,
                stop:0 #00DCFF, stop:0.52 #7B5CFF, stop:1 #FF2BD6);
        }
        #HeroTitle { color: #FFFFFF; font-size: 24px; font-weight: 900; letter-spacing: 0.6px; }
        #HeroSub   { color: rgba(234,242,255,190); font-size: 13px; }

        #Card {
            border-radius: 18px;
            background-color: rgba(13, 18, 38, 0.72);
            border: 1px solid rgba(255,255,255,0.07);
        }
        #CardTitle {
            color: rgba(234,242,255,230);
            font-weight: 900;
            font-size: 13px;
            letter-spacing: 0.4px;
        }

        #DropZoneCard {
            border-radius: 18px;
            background-color: rgba(13, 18, 38, 0.62);
            border: 1px dashed rgba(255,255,255,0.15);
        }
        #DropZoneCard[dropOn="true"] {
            border: 2px dashed rgba(0,220,255,0.95);
            background-color: rgba(0,220,255,0.08);
        }
        #DropZoneText { color: rgba(234,242,255,210); font-weight: 900; }

        QLabel { color: rgba(234,242,255,220); }
        QCheckBox { color: rgba(234,242,255,220); font-weight: 800; }

        QLineEdit, QPlainTextEdit, QComboBox, QTableWidget {
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.08);
            padding: 9px 11px;
            background-color: rgba(7, 10, 18, 0.62);
            color: rgba(234,242,255,230);
        }

        QPushButton {
            border-radius: 12px;
            padding: 10px 12px;
            background-color: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.10);
            color: rgba(234,242,255,230);
            font-weight: 900;
        }
        QPushButton:hover { border-color: rgba(0,220,255,0.45); }
        QPushButton:pressed { background-color: rgba(0,220,255,0.10); }

        #NeonCTA {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(0,220,255,0.22),
                stop:1 rgba(123,92,255,0.20));
            border: 1px solid rgba(0,220,255,0.40);
        }
        #MetalRed {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255,40,40,0.22),
                stop:1 rgba(150,0,0,0.18));
            border: 1px solid rgba(255,40,40,0.45);
        }

        #CancelBtn { border: 1px solid rgba(255,255,255,0.14); }

        QProgressBar {
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.10);
            text-align: center;
            background-color: rgba(0,0,0,0.25);
        }
        QProgressBar::chunk {
            border-radius: 10px;
            background-color: rgba(0,220,255,0.55);
        }

        #SageIconBtn {
            padding: 0px;
            margin: 0px;
            border: none;
            background: transparent;
        }
        #SageIconBtn:hover { border: none; background: transparent; }
        #SageIconBtn:pressed { border: none; background: transparent; }
        """)

    def _wire(self) -> None:
        self.btn_open_images.clicked.connect(lambda: _open_path(str(ICON_IMAGES_DIR)))  # type: ignore[arg-type]
        self.btn_clear_log.clicked.connect(self._clear_log)  # type: ignore[arg-type]

        self.edit_input.pathDropped.connect(self._set_input)  # type: ignore[arg-type]


        self.btn_browse_input.clicked.connect(self._browse_input)  # type: ignore[arg-type]

        self.btn_open_my_icons.clicked.connect(lambda: _open_path(str(ICONS_DIR)))  # type: ignore[arg-type]
        self.btn_open_images.clicked.connect(lambda: _open_path(str(ICON_IMAGES_DIR)))  # type: ignore[arg-type]

        # Scan removed; Run now does everything
        self.btn_run.clicked.connect(self._run_convert)        # type: ignore[arg-type]
        self.btn_cancel.clicked.connect(self._cancel)          # type: ignore[arg-type]

        self.mode_seg.modeChanged.connect(self._update_mode)  # type: ignore[arg-type]
        self.cmb_filter.currentTextChanged.connect(self._flush_log)  # type: ignore[arg-type]

    # ---------------- redesign helpers ----------------
    def _lock_output_to_canonical(self) -> None:
        """
        Output is deterministic: ICONS_DIR.
        We keep the Output card UI (per your requirement) but prevent changing it.
        """

        # Keep "Open Output" and "Open Icon Images" working.
        self.btn_open_images.setEnabled(True)

    # --------------- basic actions ---------------
    def _set_input(self, p: str) -> None:
        self.edit_input.setText(p)
        self.mode_seg.set_mode("folder" if Path(p).is_dir() else "file")

    def _set_outdir(self, p: str) -> None:
        # Output is locked; ignore external attempts but keep UI stable.
        self.edit_outdir.setText(str(ICONS_DIR))

    def _browse_input(self) -> None:
        mode = "folder" if self.mode_seg.btn_folder.isChecked() else "file"
        if mode == "folder":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", str(ICON_IMAGES_DIR))
            if p:
                self._set_input(p)
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose file", str(ICON_IMAGES_DIR))
            if p:
                self._set_input(p)

    def _browse_outdir(self) -> None:
        # Output locked by design. Leave this as a no-op.
        return

    def _update_mode(self, *_args) -> None:
        pass

    # --------------- logging ---------------
    def _log(self, msg: str, level: str = "INFO") -> None:
        self._log_buffer.append(LogLine(text=msg, level=level))

    def _clear_log(self) -> None:
        self.log.clear()
        self._log_buffer.clear()

    def _flush_log(self) -> None:
        if not self._log_buffer:
            return

        want = self.cmb_filter.currentText()
        lines = self._log_buffer[:]
        self._log_buffer.clear()

        out = []
        for ln in lines:
            if want != "All" and ln.level != want:
                continue
            out.append(ln.text)

        if out:
            self.log.appendPlainText("\n".join(out))
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())

    # --------------- internal maintenance scan ---------------
    def _maintenance_request(self, reason: str) -> None:
        """
        Serialized maintenance requests. If already running, remember latest reason and rerun once.
        """
        if self._maint_busy:
            self._maint_pending_reason = reason
            return
        self._maintenance_scan(reason)

    def _maintenance_scan(self, reason: str) -> None:
        """
        Internal scan ONLY (canonical-only). No UI button.
        """
        self._maint_busy = True
        self._log(f"=== MAINTENANCE ({reason}) ===")

        # keep UI responsive
        self.status_line.setText(f"Maintaining library… ({reason})")
        QtWidgets.QApplication.processEvents()

        try:
            report = eng.scan_icon_images_and_convert(
                overwrite=self.chk_overwrite.isChecked(),
                sizes=preset_sizes(self.cmb_quality.currentText()),
                padding_mode=self.cmb_padding.currentText(),
                autocrop=False,
                logfn=lambda s: self._log(s),
                remove_orphans=True,
                orphan_action="delete",
            )

            msg = (
                "Maintenance done. "
                f"scanned={report.scanned} "
                f"converted={report.converted} "
                f"errors={report.errors} "
                f"orphans_removed={report.orphan_icons_removed} "
                f"normalized_moves={report.normalized_moves}"
            )
            self._log(msg)
            self.status_line.setText(f"Maintenance done. {report.converted} converted.")
        except Exception as e:
            self._log(f"ERR: Maintenance failed: {e}", "ERR")
            self.status_line.setText("Maintenance failed.")
        finally:
            self._maint_busy = False

            # if something came in while busy, rerun once
            if self._maint_pending_reason:
                r = self._maint_pending_reason
                self._maint_pending_reason = None
                QtCore.QTimer.singleShot(50, lambda: self._maintenance_request(r))  # type: ignore[arg-type]

    def _arm_icon_images_watcher(self) -> None:
        """
        Watch Icon Images/ for changes. Debounce to avoid storms.
        """
        try:
            ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # Ensure directory is watched
        watched_dirs = set(self._fs_watcher.directories())
        if str(ICON_IMAGES_DIR) not in watched_dirs:
            try:
                self._fs_watcher.addPath(str(ICON_IMAGES_DIR))
            except Exception:
                pass

        # Also watch current root files (helps catch replace/rename patterns)
        watched_files = set(self._fs_watcher.files())
        try:
            for p in ICON_IMAGES_DIR.iterdir():
                if p.is_file() and str(p) not in watched_files:
                    try:
                        self._fs_watcher.addPath(str(p))
                    except Exception:
                        continue
        except Exception:
            pass

    def _on_icon_images_fs_event(self, _path: str) -> None:
        """
        Any file add/remove/rename/modify inside Icon Images triggers a debounced maintenance run.
        """
        self._arm_icon_images_watcher()
        self._fs_debounce.start()

    # --------------- conversion actions ---------------
    def _cancel(self) -> None:
        self._cancel_requested = True
        self.btn_cancel.setEnabled(False)
        self._log("Cancel requested (will stop after current file).", "WARN")

    def _run_convert(self) -> None:
        """
        Run = COPY into Icon Images/ → convert copied files → post-run maintenance
        Output is ALWAYS ICONS_DIR.
        """
        self._cancel_requested = False
        self.btn_cancel.setEnabled(True)
        self.bar.setValue(0)

        inp = Path(self.edit_input.text().strip())
        out_dir = Path(ICONS_DIR)  # canonical
        out_dir.mkdir(parents=True, exist_ok=True)
        ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        if not str(inp):
            self._log("ERR: No input provided.", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        sizes = preset_sizes(self.cmb_quality.currentText())

        # enforce ceiling & accepted ladder
        sizes = [s for s in sizes if 1 <= s <= 1024]
        if not sizes:
            sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]

        padding_mode = self.cmb_padding.currentText()
        overwrite = self.chk_overwrite.isChecked()
        recursive = self.chk_recursive.isChecked()

        self._log("=== RUN ===")
        self._log(f"Input: {inp}")
        self._log(f"Output (canonical): {out_dir}")
        self._log(f"Sizes: {sizes}")
        self._log(f"Padding: {padding_mode}")
        self._log(f"Overwrite: {overwrite} | Recursive: {recursive} | Mirror: True (forced)")

        if not inp.exists():
            self._log("ERR: Input path does not exist.", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        # Gather external images (source)
        images = _gather_images(inp, recursive=recursive)
        if not images:
            self._log("ERR: No images found for conversion.", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        total = len(images)
        converted = 0
        copied_ok = 0

        # Convert one-by-one from COPIED library files (cancellable)
        try:
            for i, src in enumerate(images, start=1):
                if self._cancel_requested:
                    self._log("Stopped by cancel.", "WARN")
                    break

                self.status_line.setText(f"Copying {i}/{total}: {src.name}")
                self.bar.setValue(int((i - 1) * 100 / max(1, total)))
                QtWidgets.QApplication.processEvents()

                # COPY into Icon Images library (canonical)
                try:
                    dst = eng.mirror_copy_to_icon_images(src, logfn=lambda s: self._log(s))
                except Exception as e:
                    dst = None
                    self._log(f"ERR: Copy failed: {src.name}: {e}", "ERR")

                if not dst:
                    continue

                copied_ok += 1

                # Convert the COPIED file to ICONS_DIR (canonical)
                self.status_line.setText(f"Converting {i}/{total}: {Path(dst).name}")
                QtWidgets.QApplication.processEvents()

                try:
                    eng.make_ico(
                        Path(dst),
                        out_dir,
                        sizes=sizes,
                        overwrite=overwrite,
                        padding_mode=padding_mode,
                        autocrop=False,
                        logfn=lambda s: self._log(s),
                    )
                    converted += 1
                except Exception as e:
                    self._log(f"ERR: {Path(dst).name}: {e}", "ERR")

            # post-run maintenance scan (canonical-only)
            self._maintenance_request("post-run")

            if self._cancel_requested:
                self.status_line.setText(f"Stopped. copied={copied_ok} converted={converted}/{total}.")
            else:
                self.status_line.setText(f"Done. copied={copied_ok} converted={converted}/{total}.")
                self.bar.setValue(100)

            self._log(self.status_line.text())

        except Exception as e:
            self._log(f"ERR: {e}", "ERR")
            self.status_line.setText("Run failed.")
            self.bar.setValue(0)
        finally:
            self.btn_cancel.setEnabled(False)

    # --------------- drag/drop window ---------------
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            self.drop_zone.setProperty("dropOn", True)
            _repolish(self.drop_zone)
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragLeaveEvent(self, e: QtGui.QDragLeaveEvent) -> None:
        self.drop_zone.setProperty("dropOn", False)
        _repolish(self.drop_zone)
        super().dragLeaveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        self.drop_zone.setProperty("dropOn", False)
        _repolish(self.drop_zone)

        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if p:
                    self._set_input(p)
                    e.acceptProposedAction()
                    return
        super().dropEvent(e)

    # --------------- shutdown maintenance ---------------
    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        # best-effort maintenance on shutdown
        try:
            self._maintenance_request("shutdown")
        except Exception:
            pass
        super().closeEvent(e)


def main() -> None:
    _pre_app_setup()

    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(get_app_icon())

    w = MainWindow()
    w.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
