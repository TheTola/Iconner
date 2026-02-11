from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtWidgets


@dataclass(frozen=True)
class StateKeys:
    last_input: str = "last_input"
    last_mode: str = "last_mode"              # "file" | "folder"
    last_input_dir: str = "last_input_dir"
    last_output_dir: str = "last_output_dir"
    last_recursive: str = "last_recursive"
    last_overwrite: str = "last_overwrite"
    last_padding: str = "last_padding"


class StateMemory:
    """
    Centralized state persistence for Gen1 using QSettings.

    Assumes the MainWindow exposes these attributes (names can be adjusted):
      - input_edit: QLineEdit
      - mode_file_btn / mode_folder_btn OR get_mode()/set_mode(mode)
      - btn_browse_input: QPushButton
      - output_edit: QLineEdit (optional / if you show it)
      - chk_recursive: QCheckBox (optional)
      - chk_overwrite: QCheckBox (optional)
      - cmb_padding: QComboBox (optional)
    """

    def __init__(self, org: str, app: str):
        self.settings = QtCore.QSettings(org, app)
        self.k = StateKeys()

    # ----------------- Mode helpers -----------------

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        m = (mode or "").strip().lower()
        return "folder" if m == "folder" else "file"

    def get_mode_from_ui(self, w: QtWidgets.QWidget) -> str:
        # Prefer explicit methods if you have them
        if hasattr(w, "get_mode") and callable(getattr(w, "get_mode")):
            return self._normalize_mode(w.get_mode())
        # Fall back to common button patterns
        if hasattr(w, "btn_mode_folder") and getattr(w, "btn_mode_folder").isChecked():
            return "folder"
        if hasattr(w, "mode_folder_btn") and getattr(w, "mode_folder_btn").isChecked():
            return "folder"
        return "file"

    def set_mode_to_ui(self, w: QtWidgets.QWidget, mode: str) -> None:
        mode = self._normalize_mode(mode)
        if hasattr(w, "set_mode") and callable(getattr(w, "set_mode")):
            w.set_mode(mode)
        else:
            # Best-effort toggle buttons if present
            for name, should_check in (
                ("btn_mode_file", mode == "file"),
                ("btn_mode_folder", mode == "folder"),
                ("mode_file_btn", mode == "file"),
                ("mode_folder_btn", mode == "folder"),
            ):
                if hasattr(w, name):
                    btn = getattr(w, name)
                    try:
                        btn.setChecked(should_check)
                    except Exception:
                        pass

        self.apply_truthful_source_ui(w, mode)

    # ----------------- Truthful UI -----------------

    def apply_truthful_source_ui(self, w: QtWidgets.QWidget, mode: Optional[str] = None) -> None:
        mode = self._normalize_mode(mode or self.get_mode_from_ui(w))

        # Button label reflects mode
        if hasattr(w, "btn_browse_input"):
            try:
                w.btn_browse_input.setText("Select Image…" if mode == "file" else "Select Folder…")
            except Exception:
                pass

        # Placeholder reflects mode
        if hasattr(w, "input_edit"):
            try:
                w.input_edit.setPlaceholderText(
                    "Drop an image here or click Select Image…" if mode == "file"
                    else "Drop a folder here or click Select Folder…"
                )
            except Exception:
                pass

    # ----------------- Load / Save -----------------

    def load_to_ui(self, w: QtWidgets.QWidget) -> None:
        mode = self._normalize_mode(self.settings.value(self.k.last_mode, "file"))
        last_input = str(self.settings.value(self.k.last_input, "") or "")
        last_out = str(self.settings.value(self.k.last_output_dir, "") or "")

        # Set mode first so UI updates correctly
        self.set_mode_to_ui(w, mode)

        # Input path
        if hasattr(w, "input_edit") and last_input:
            try:
                w.input_edit.setText(last_input)
            except Exception:
                pass

        # Output path (if you display it)
        if hasattr(w, "output_edit") and last_out:
            try:
                w.output_edit.setText(last_out)
            except Exception:
                pass

        # Flags
        if hasattr(w, "chk_recursive"):
            try:
                w.chk_recursive.setChecked(bool(self.settings.value(self.k.last_recursive, False, type=bool)))
            except Exception:
                pass

        if hasattr(w, "chk_overwrite"):
            try:
                w.chk_overwrite.setChecked(bool(self.settings.value(self.k.last_overwrite, True, type=bool)))
            except Exception:
                pass

        if hasattr(w, "cmb_padding"):
            try:
                pad = str(self.settings.value(self.k.last_padding, "") or "")
                if pad:
                    idx = w.cmb_padding.findText(pad)
                    if idx >= 0:
                        w.cmb_padding.setCurrentIndex(idx)
            except Exception:
                pass

        # Ensure UI stays truthful even if paths are empty
        self.apply_truthful_source_ui(w, mode)

    def save_from_ui(self, w: QtWidgets.QWidget) -> None:
        mode = self.get_mode_from_ui(w)

        input_path = ""
        if hasattr(w, "input_edit"):
            try:
                input_path = w.input_edit.text().strip()
            except Exception:
                input_path = ""

        out_path = ""
        if hasattr(w, "output_edit"):
            try:
                out_path = w.output_edit.text().strip()
            except Exception:
                out_path = ""

        self.settings.setValue(self.k.last_mode, mode)
        self.settings.setValue(self.k.last_input, input_path)

        # Store “last input dir” for dialog defaulting
        in_dir = self._dir_for_path(input_path, fallback=self.settings.value(self.k.last_input_dir, "") or "")
        if in_dir:
            self.settings.setValue(self.k.last_input_dir, in_dir)

        if out_path:
            self.settings.setValue(self.k.last_output_dir, out_path)

        if hasattr(w, "chk_recursive"):
            try:
                self.settings.setValue(self.k.last_recursive, bool(w.chk_recursive.isChecked()))
            except Exception:
                pass

        if hasattr(w, "chk_overwrite"):
            try:
                self.settings.setValue(self.k.last_overwrite, bool(w.chk_overwrite.isChecked()))
            except Exception:
                pass

        if hasattr(w, "cmb_padding"):
            try:
                self.settings.setValue(self.k.last_padding, str(w.cmb_padding.currentText()))
            except Exception:
                pass

        self.settings.sync()

    # ----------------- Auto-save wiring -----------------

    def install_auto_save(self, w: QtWidgets.QWidget) -> None:
        """
        Connects common signals to save state as the user interacts.
        You still should call save_from_ui() in closeEvent as a final guarantee.
        """
        # Mode buttons
        for name in ("btn_mode_file", "btn_mode_folder", "mode_file_btn", "mode_folder_btn"):
            if hasattr(w, name):
                btn = getattr(w, name)
                try:
                    btn.toggled.connect(lambda _=False: self._on_any_change(w))
                except Exception:
                    pass

        # Input edit changes
        if hasattr(w, "input_edit"):
            try:
                w.input_edit.textChanged.connect(lambda _=None: self._on_any_change(w))
            except Exception:
                pass

        # Output edit changes
        if hasattr(w, "output_edit"):
            try:
                w.output_edit.textChanged.connect(lambda _=None: self._on_any_change(w))
            except Exception:
                pass

        # Flags
        for name in ("chk_recursive", "chk_overwrite"):
            if hasattr(w, name):
                chk = getattr(w, name)
                try:
                    chk.stateChanged.connect(lambda _=None: self._on_any_change(w))
                except Exception:
                    pass

        if hasattr(w, "cmb_padding"):
            try:
                w.cmb_padding.currentIndexChanged.connect(lambda _=None: self._on_any_change(w))
            except Exception:
                pass

    def _on_any_change(self, w: QtWidgets.QWidget) -> None:
        mode = self.get_mode_from_ui(w)
        self.apply_truthful_source_ui(w, mode)
        self.save_from_ui(w)

    # ----------------- Utilities -----------------

    @staticmethod
    def _dir_for_path(path_str: str, fallback: str = "") -> str:
        p = Path(path_str) if path_str else None
        if p:
            try:
                if p.exists():
                    return str(p if p.is_dir() else p.parent)
                # even if it doesn't exist, still derive a directory
                return str(p.parent if p.suffix else p)
            except Exception:
                pass
        return str(fallback or "")
