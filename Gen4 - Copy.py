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

        # ---------------- Persistent UI state ----------------
        self.state = StateMemory(APP_ORG, APP_NAME)
        self.state.load_to_ui(self)
        self.state.install_auto_save(self)

        # Apply canonical paths BEFORE watchers/UI
        self._set_library_paths(root)
        self.DEFAULT_OUTPUT_DIR = str(ICONS_DIR)

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

        # --- logging buffers ---
        # Keep ALL log history for filter rebuild. Use pending buffer for batched UI appends.
        self._log_history: list[LogLine] = []
        self._log_pending: list[LogLine] = []

        self._log_flush_timer = QtCore.QTimer(self)
        self._log_flush_timer.setInterval(80)
        self._log_flush_timer.timeout.connect(self._flush_log_pending)
        self._log_flush_timer.start()

        self._cancel_requested = False

        # Build UI first
        self._build_ui()
        self._apply_theme()
        self._wire()

        # ---------------- Restore UI values ----------------
        last_input = self._settings.value("last_input", "", str)
        if last_input:
            self.edit_input.setText(last_input)
            p = Path(last_input)
            self.mode_seg.set_mode("folder" if p.is_dir() else "Image")

        self.chk_recursive.setChecked(self._settings.value("last_recursive", False, bool))
        self.chk_overwrite.setChecked(self._settings.value("last_overwrite", True, bool))

        pad = self._settings.value("last_padding", "balanced", str)
        if self.cmb_padding.findText(pad) >= 0:
            self.cmb_padding.setCurrentText(pad)

        qual = self._settings.value("last_quality", "16–1024", str)
        if self.cmb_quality.findText(qual) >= 0:
            self.cmb_quality.setCurrentText(qual)

        # ---------------- Persist on change ----------------
        self.edit_input.textChanged.connect(lambda v: self._settings.setValue("last_input", v))
        self.chk_recursive.toggled.connect(lambda b: self._settings.setValue("last_recursive", b))
        self.chk_overwrite.toggled.connect(lambda b: self._settings.setValue("last_overwrite", b))
        self.cmb_padding.currentTextChanged.connect(lambda t: self._settings.setValue("last_padding", t))
        self.cmb_quality.currentTextChanged.connect(lambda t: self._settings.setValue("last_quality", t))
        self.mode_seg.modeChanged.connect(lambda m: self._settings.setValue("last_mode", m))

        # Apply truthful UI rules after widgets exist
        try:
            self.state.apply_truthful_source_ui(self)
        except Exception:
            pass

        self._log("Ready.")
        self._lock_output_to_canonical()
        self._arm_icon_images_watcher()
        self._update_mode()

    # ---------------- core: canonical paths ----------------
    def _set_library_paths(self, library_root: Path) -> None:
        """Set canonical library paths for the UI + engine.

        Single source of truth:
        - MainWindow state
        - Gen1 module globals (ICON_IMAGES_DIR/ICONS_DIR)
        - Gen2 engine globals
        - Gen4 helper globals
        """
        root = Path(library_root).resolve()
        self.LIBRARY_ROOT = root

        icon_images = root / "Icon Images"
        icons = icon_images / "Icons"
        icon_images.mkdir(parents=True, exist_ok=True)
        icons.mkdir(parents=True, exist_ok=True)

        # Update THIS module globals (used throughout Gen1)
        global ICON_IMAGES_DIR, ICONS_DIR
        ICON_IMAGES_DIR = icon_images
        ICONS_DIR = icons

        # Update engine globals (Gen2)
        try:
            eng.ICONER_ROOT = root
            eng.ICON_IMAGES_DIR = icon_images
            eng.ICONS_DIR = icons
        except Exception:
            pass

        # Update helper globals (Gen4)
        try:
            import Gen4 as g4
            g4.ICONER_ROOT = root
            g4.ICON_IMAGES_DIR = icon_images
            g4.ICONS_DIR = icons
        except Exception:
            pass

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
            self.mark.setPixmap(
                pm.scaled(38, 38, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            )
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
        source = CardFrame("Select Image or Folder of Images:")
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

        self.lbl_outdir = QtWidgets.QLabel(str(ICONS_DIR))
        self.lbl_outdir.setObjectName("FixedOutPath")
        self.lbl_outdir.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.btn_open_my_icons = QtWidgets.QPushButton("Open My Icons")
        self.btn_open_my_icons.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_open_images = QtWidgets.QPushButton("Open Icon Images")
        self.btn_open_images.setCursor(QtCore.Qt.PointingHandCursor)

        self.btn_change_library = QtWidgets.QPushButton("Change Library Location…")
        self.btn_change_library.setCursor(QtCore.Qt.PointingHandCursor)

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

        /* Mode Buttons - Default */
        #ModeImageBtn, #ModeFolderBtn {
            border-radius: 12px;
            padding: 10px 12px;
            font-weight: 900;
            background-color: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.10);
            color: rgba(234,242,255,230);
        }

        /* Image Selected */
        #ModeImageBtn:checked {
            background-color: #EFBF04;
            border: 1px solid #EFBF04;
            color: black;
        }

        /* Folder Selected */
        #ModeFolderBtn:checked {
            background-color: #C0C0C0;
            border: 1px solid #C0C0C0;
            color: black;
        }
        """)

    def _wire(self) -> None:
        self.btn_clear_log.clicked.connect(self._clear_log)  # type: ignore[arg-type]
        self.btn_browse_input.clicked.connect(self._browse_input)  # type: ignore[arg-type]

        self.edit_input.pathDropped.connect(self._set_input)  # type: ignore[arg-type]

        self.btn_open_my_icons.clicked.connect(lambda: _open_path(str(ICONS_DIR)))  # type: ignore[arg-type]
        self.btn_open_images.clicked.connect(lambda: _open_path(str(ICON_IMAGES_DIR)))  # type: ignore[arg-type]
        self.btn_change_library.clicked.connect(self._change_library_location)  # type: ignore[arg-type]

        # Scan removed; Run now does everything
        self.btn_run.clicked.connect(self._run_convert)        # type: ignore[arg-type]
        self.btn_cancel.clicked.connect(self._cancel)          # type: ignore[arg-type]

        self.mode_seg.modeChanged.connect(self._update_mode)   # type: ignore[arg-type]
        self.cmb_filter.currentTextChanged.connect(self._rebuild_log_view)  # type: ignore[arg-type]

    # ---------------- redesign helpers ----------------
    def _lock_output_to_canonical(self) -> None:
        """Output is deterministic: ICONS_DIR. Output card remains visible but cannot be changed."""
        # (Nothing to unlock. Buttons are for opening folders only.)
        try:
            self.lbl_outdir.setText(str(ICONS_DIR))
        except Exception:
            pass

    def _update_mode(self, *_args) -> None:
        """UI-only mode toggle.

        - Folder mode enables Recursive checkbox.
        - File mode disables Recursive checkbox (and turns it off).
        """
        try:
            mode = "folder" if self.mode_seg.btn_folder.isChecked() else "Image"
        except Exception:
            mode = "Image"

        if mode == "folder":
            self.chk_recursive.setEnabled(True)
        else:
            self.chk_recursive.setChecked(False)
            self.chk_recursive.setEnabled(False)

    # ---------------- logging ----------------
    def _log(self, msg: str, level: str = "INFO") -> None:
        item = LogLine(text=msg, level=level)
        self._log_history.append(item)
        self._log_pending.append(item)

    def _clear_log(self) -> None:
        self.log.clear()
        self._log_history.clear()
        self._log_pending.clear()

    def _passes_filter(self, item: LogLine, filt: str) -> bool:
        return (filt == "All") or (item.level == filt)

    def _rebuild_log_view(self) -> None:
        """Re-render the whole log view from history when filter changes."""
        filt = self.cmb_filter.currentText() if hasattr(self, "cmb_filter") else "All"
        self.log.blockSignals(True)
        try:
            self.log.setPlainText(
                "\n".join(
                    f"[{it.level}] {it.text}"
                    for it in self._log_history
                    if self._passes_filter(it, filt)
                )
            )
        finally:
            self.log.blockSignals(False)

    def _flush_log_pending(self) -> None:
        """Append pending lines that match the current filter; keep history intact."""
        if not self._log_pending:
            return

        filt = self.cmb_filter.currentText() if hasattr(self, "cmb_filter") else "All"

        lines = []
        for it in self._log_pending:
            if self._passes_filter(it, filt):
                lines.append(f"[{it.level}] {it.text}")

        self._log_pending.clear()

        if lines:
            self.log.appendPlainText("\n".join(lines))

    # ---------------- basic actions ----------------
    def _set_input(self, p: str) -> None:
        self.edit_input.setText(p)
        self.mode_seg.set_mode("folder" if Path(p).is_dir() else "Image")
        self._update_mode()

    def _browse_input(self) -> None:
        mode = "folder" if self.mode_seg.btn_folder.isChecked() else "Image"
        if mode == "folder":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose folder", str(ICON_IMAGES_DIR))
            if p:
                self._set_input(p)
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Choose Image", str(ICON_IMAGES_DIR))
            if p:
                self._set_input(p)

    # ---------------- internal maintenance scan ----------------
    def _maintenance_request(self, reason: str) -> None:
        """Serialized maintenance requests. If already running, remember latest reason and rerun once."""
        if self._maint_busy:
            self._maint_pending_reason = reason
            return
        self._maintenance_scan(reason)

    def _maintenance_scan(self, reason: str) -> None:
        """Internal scan ONLY (canonical-only). No UI button."""
        self._maint_busy = True
        self._log(f"=== MAINTENANCE ({reason}) ===")

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

            # If something came in while busy, rerun once
            if self._maint_pending_reason:
                r = self._maint_pending_reason
                self._maint_pending_reason = None
                QtCore.QTimer.singleShot(50, lambda: self._maintenance_request(r))  # type: ignore[arg-type]

    def _arm_icon_images_watcher(self) -> None:
        """Watch Icon Images/ for changes. Debounce to avoid storms."""
        try:
            ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        watched_dirs = set(self._fs_watcher.directories())
        if str(ICON_IMAGES_DIR) not in watched_dirs:
            try:
                self._fs_watcher.addPath(str(ICON_IMAGES_DIR))
            except Exception:
                pass

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

    def _change_library_location(self) -> None:
        """Move the entire library to a new location (copy → switch → optional delete)."""
        try:
            old_root = Path(self.LIBRARY_ROOT)
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

        if not old_root:
            self._log("ERR: Could not determine current library root.", "ERR")
            return

        dlg = QtWidgets.QProgressDialog("Relocating library…", "Cancel", 0, 100, self)
        dlg.setWindowTitle("IconMaker")
        dlg.setWindowModality(QtCore.Qt.WindowModal)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.show()

        thread = QtCore.QThread(self)
        worker = LibraryRelocateWorker(old_root, new_root)
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

            # Switch canonical paths (single source of truth)
            self._settings.setValue("library_root", str(new_root))
            self._set_library_paths(new_root)

            # Update UI path label immediately
            try:
                self.lbl_outdir.setText(str(ICONS_DIR))
            except Exception:
                pass

            # Re-arm watcher for the new folder
            self._arm_icon_images_watcher()

            self._log(f"Library relocated to: {new_root}")

            # Offer to delete old root (turn copy into MOVE)
            r = QtWidgets.QMessageBox.question(
                self,
                "IconMaker",
                "Copy complete.\n\nDelete the OLD library folder to finish the move?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if r == QtWidgets.QMessageBox.Yes and old_root.exists():
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

    def _on_icon_images_fs_event(self, _path: str) -> None:
        """Any file add/remove/rename/modify inside Icon Images triggers a debounced maintenance run."""
        self._arm_icon_images_watcher()
        self._fs_debounce.start()

    # ---------------- conversion actions ----------------
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

        inp_txt = self.edit_input.text().strip()
        if not inp_txt:
            self._log("ERR: No input provided.", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        inp = Path(inp_txt)
        out_dir = Path(ICONS_DIR)  # canonical
        out_dir.mkdir(parents=True, exist_ok=True)
        ICON_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

        sizes = preset_sizes(self.cmb_quality.currentText())
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

        images = _gather_images(inp, recursive=recursive)
        if not images:
            self._log("ERR: No images found for conversion.", "ERR")
            try:
                rpt = eng.diagnose_image_discovery(inp, recursive=recursive)
                for line in rpt.to_lines():
                    self._log(line, "ERR")
            except Exception as e:
                self._log(f"ERR: Diagnosis failed: {type(e).__name__}: {e}", "ERR")
            self.btn_cancel.setEnabled(False)
            return

        total = len(images)
        converted = 0
        copied_ok = 0

        try:
            for i, src in enumerate(images, start=1):
                if self._cancel_requested:
                    self._log("Stopped by cancel.", "WARN")
                    break

                self.status_line.setText(f"Copying {i}/{total}: {src.name}")
                self.bar.setValue(int((i - 1) * 100 / max(1, total)))
                QtWidgets.QApplication.processEvents()

                try:
                    dst = eng.mirror_copy_to_icon_images(src, logfn=lambda s: self._log(s))
                except Exception as e:
                    dst = None
                    self._log(f"ERR: Copy failed: {src.name}: {e}", "ERR")

                if not dst:
                    continue

                copied_ok += 1

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

            # Post-run maintenance scan (canonical-only)
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

    # ---------------- drag/drop window ----------------
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

    # ---------------- shutdown ----------------
    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        # Best-effort: save UI state
        try:
            self.state.save_from_ui(self)
        except Exception:
            pass

        # Best-effort: queue a maintenance request (debounced/serialized)
        try:
            self._maintenance_request("shutdown")
        except Exception:
            pass

        super().closeEvent(e)
