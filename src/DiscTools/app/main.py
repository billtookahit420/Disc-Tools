"""Disc Tools -- an ImgBurn-class disc app for Linux.

A GUI over the open-source disc engines -- xorriso, growisofs,
cdrdao, GNU ddrescue and redumper -- organised the way ImgBurn
organises its work: Write, Build, Read, Verify and Discovery,
with a job queue (a failed or hung job never kills it), a
narrating status bar, a parsed results view plus the raw engine
log per job, and a live command preview.

First run sets the engines up by itself: distro packages are
detected (and offered as a one-click install when missing --
never a dead-end), redumper is downloaded and hash-verified.
Everything lives under ~/.local/share/disc-tools/.

Xbox 360: check the ISO with the ABGX360 GUI first, burn it
here (XGD2 / XGD3 layer-break presets included), then Verify.
The presets burn through growisofs' break flag and are marked
BENCH-PROOF-PENDING until a burned disc reads its break back.

Credits: xorriso/libburnia (Thomas Schmitt), redumper (superg),
cdrdao, GNU ddrescue. ImgBurn is the design reference only.
"""
import json
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import disc_engine as engine  # noqa: E402

from PySide6.QtCore import Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QRadioButton, QSpinBox, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)


class Worker(QThread):
    """Runs one engine callable in the background. Workers are
    held in a set on the window until they finish."""
    log = Signal(str)
    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn(lambda s: self.log.emit(str(s)))
        except Exception as e:  # noqa: BLE001
            self.done.emit({"error": "%s: %s" % (type(e).__name__, e)})
            return
        self.done.emit(result)


SPEEDS = ["max", "1", "2", "4", "6", "8", "10", "12", "16", "24"]


class ShareDialog(QDialog):
    """Ask for an SMB share: server + share name, with the
    remembered servers/shares and the recent list offered back.
    A full reference (smb://server/share/sub or
    \\\\server\\share\\sub) pasted into the Server field is
    parsed and split into its parts. The password is used for
    this mount attempt only -- it is never written to settings
    or anywhere else."""

    def __init__(self, parent, net):
        super().__init__(parent)
        self.setWindowTitle("Network Share")
        self._net = net or {}
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.server = QComboBox()
        self.server.setEditable(True)
        self.server.addItems(self._net.get("servers", []))
        self.share = QComboBox()
        self.share.setEditable(True)
        self.share.addItems(self._shares_for(
            self.server.currentText()))
        self.user = QLineEdit()
        self.user.setPlaceholderText("Optional -- blank = you")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText(
            "Optional -- never saved")
        form.addRow("Server:", self.server)
        form.addRow("Share:", self.share)
        form.addRow("User:", self.user)
        form.addRow("Password:", self.password)
        lay.addLayout(form)
        lay.addWidget(QLabel("Recent shares (double-click):"))
        self.recent = QListWidget()
        for ref in self._net.get("recent", []):
            self.recent.addItem(QListWidgetItem(ref))
        lay.addWidget(self.recent)
        note = QLabel(
            "Linux mounts the share through GVFS (gio) and the "
            "picker then browses the mounted folder -- the disc "
            "engines only ever see a real local path. On Windows "
            "the \\\\server\\share path is used directly.")
        note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.server.currentTextChanged.connect(
            self._server_changed)
        self.recent.itemDoubleClicked.connect(
            self._recent_picked)

    def _shares_for(self, server):
        return (self._net.get("shares", {})
                .get(server.strip(), []))

    def _server_changed(self, text):
        ref = engine.parse_share_ref(text)
        if ref:
            if self.share.currentText() != ref["share"]:
                self.share.setCurrentText(ref["share"])
            return
        current = self.share.currentText()
        self.share.clear()
        self.share.addItems(self._shares_for(text))
        if current:
            self.share.setCurrentText(current)

    def _recent_picked(self, item):
        ref = engine.parse_share_ref("smb://" + item.text())
        if ref:
            self.server.setCurrentText(ref["server"])
            self.share.setCurrentText(ref["share"])

    def values(self):
        server = self.server.currentText().strip()
        share = self.share.currentText().strip()
        subpath = ""
        ref = engine.parse_share_ref(server)
        if ref:
            server = ref["server"]
            if not share:
                share = ref["share"]
            subpath = ref["subpath"]
        elif engine.parse_share_ref(share):
            ref = engine.parse_share_ref(share)
            share = ref["share"]
            subpath = ref["subpath"]
        return {"server": server, "share": share,
                "subpath": subpath,
                "user": self.user.text().strip(),
                "password": self.password.text()}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Disc Tools")
        self.resize(1060, 880)
        self._workers = set()
        self.items = []            # queue item dicts
        self._running = False
        self._stop = False
        self._control = {}
        self._setup_done = False
        self._setup_error = None
        self._setup_stopped = False
        self._dep_offer_rounds = 0
        self._dep_offers = []
        self._drives = []
        self._scan_done = False
        self._ui_queue = []
        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._drain_ui_queue)
        self._ui_timer.start(100)
        self._build_ui()
        try:
            engine.clean_archive_scratch()
        except Exception:  # noqa: BLE001
            pass
        self._load_settings()
        self._update_preview()
        self._start_setup()
        self._scan_drives()

    # ----------------------------------------------------------
    # UI construction
    # ----------------------------------------------------------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # --- queue panel ---
        qbox = QGroupBox("Job Queue")
        qlay = QVBoxLayout(qbox)
        btns = QHBoxLayout()
        self.btn_remove = QPushButton("Remove Selected")
        self.btn_clear = QPushButton("Clear Queue")
        btns.addWidget(self.btn_remove)
        btns.addWidget(self.btn_clear)
        btns.addStretch(1)
        self.btn_run = QPushButton("Run Queue")
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        btns.addWidget(self.btn_run)
        btns.addWidget(self.btn_stop)
        qlay.addLayout(btns)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Job", "Status", "Result", "Detail"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        qlay.addWidget(self.table)
        root.addWidget(qbox)

        # --- mode tabs + results ---
        split = QSplitter(Qt.Orientation.Vertical)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_write(), "Write")
        self.tabs.addTab(self._tab_build(), "Build")
        self.tabs.addTab(self._tab_read(), "Read")
        self.tabs.addTab(self._tab_verify(), "Verify")
        self.tabs.addTab(self._tab_discovery(), "Discovery")
        split.addWidget(self.tabs)

        rbox = QWidget()
        rlay = QVBoxLayout(rbox)
        rlay.setContentsMargins(0, 4, 0, 0)
        self.results = QTableWidget(0, 3)
        self.results.setHorizontalHeaderLabels(
            ["Check", "Result", "Detail"])
        self.results.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.results.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        rlay.addWidget(self.results)
        self.raw_log = QPlainTextEdit()
        self.raw_log.setReadOnly(True)
        self.raw_log.setPlaceholderText(
            "Raw engine output for the selected job appears here.")
        rlay.addWidget(self.raw_log)
        split.addWidget(rbox)
        split.setSizes([430, 300])
        root.addWidget(split, 1)

        # --- live command preview ---
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Command:"))
        self.preview = QLineEdit()
        self.preview.setReadOnly(True)
        prow.addWidget(self.preview, 1)
        root.addLayout(prow)

        # --- status bar ---
        self.stage_label = QLabel("Starting...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(220)
        status_bar = self.statusBar()
        status_bar.addWidget(self.stage_label, 1)
        status_bar.addPermanentWidget(self.progress_bar)

        self._build_menus()
        self._wire()

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("File")
        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)
        help_menu = mb.addMenu("Help")
        act_setup = QAction("Re-run first-run setup", self)
        act_setup.triggered.connect(lambda: self._start_setup())
        help_menu.addAction(act_setup)
        act_about = QAction("About Disc Tools", self)
        act_about.triggered.connect(self._about)
        help_menu.addAction(act_about)

    def _about(self):
        QMessageBox.information(
            self, "About Disc Tools",
            "Disc Tools -- an ImgBurn-class disc app for Linux.\n\n"
            "Engines: xorriso / libburnia (Thomas Schmitt), "
            "growisofs (dvd+rw-tools), cdrdao, GNU ddrescue, "
            "redumper (superg).\n\n"
            "Xbox 360 layer-break presets (XGD2 1913760, XGD3 "
            "2084960) burn via growisofs' break flag and are "
            "BENCH-PROOF-PENDING until a burned disc reads its "
            "break back on real hardware.\n\n"
            "The stock-drive BurnerMax payload unlock is phase 2 "
            "and is not in this build; flashed drives report "
            "their full capacity themselves and work as-is.\n\n"
            "Every file picker has a Network... button: it "
            "mounts an SMB share from a file server (GVFS on "
            "Linux, the UNC path on Windows) and browses the "
            "real mounted folder, so images can be picked "
            "straight off the server and dumps saved back to "
            "it.\n\n"
            "ImgBurn is the design reference only -- no code, "
            "assets or text from it.")

    def _device_combo(self):
        combo = QComboBox()
        combo.setEditable(True)
        combo.setMinimumWidth(300)
        return combo

    def _path_row(self, parent_layout, label, placeholder=""):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        edit = QLineEdit()
        if placeholder:
            edit.setPlaceholderText(placeholder)
        btn = QPushButton("Browse...")
        net_btn = QPushButton("Network...")
        net_btn.setToolTip(
            "Browse a network (SMB) share -- e.g. the home "
            "server where the disc images live")
        row.addWidget(edit, 1)
        row.addWidget(btn)
        row.addWidget(net_btn)
        parent_layout.addLayout(row)
        return edit, btn, net_btn

    # ----------------------------------------------------------
    # Write tab
    # ----------------------------------------------------------
    def _tab_write(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        self.wr_image, self.wr_image_btn, self.wr_image_net = self._path_row(
            lay, "Image file:",
            "ISO or BIN/CUE image (.dvd sidecar is read "
            "automatically)")
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Destination drive:"))
        self.wr_device = self._device_combo()
        drow.addWidget(self.wr_device, 1)
        drow.addWidget(QLabel("Engine:"))
        self.wr_engine = QComboBox()
        self.wr_engine.addItems(
            ["auto", "growisofs", "xorriso", "cdrdao"])
        drow.addWidget(self.wr_engine)
        drow.addWidget(QLabel("Write speed:"))
        self.wr_speed = QComboBox()
        self.wr_speed.addItems(SPEEDS)
        drow.addWidget(self.wr_speed)
        drow.addWidget(QLabel("Copies:"))
        self.wr_copies = QSpinBox()
        self.wr_copies.setRange(1, 20)
        drow.addWidget(self.wr_copies)
        lay.addLayout(drow)

        lb_box = QGroupBox("Layer break (DVD+R DL)")
        lbl = QVBoxLayout(lb_box)
        self.wr_lb_group = QButtonGroup(self)
        self.wr_lb_none = QRadioButton("None")
        self.wr_lb_xgd2 = QRadioButton(
            "XGD2 preset (%d)" % engine.XGD2_BREAK)
        self.wr_lb_xgd3 = QRadioButton(
            "XGD3 preset (%d)" % engine.XGD3_BREAK)
        self.wr_lb_dvd = QRadioButton("From .dvd file")
        self.wr_lb_custom = QRadioButton("Custom:")
        self.wr_lb_none.setChecked(True)
        for rb in (self.wr_lb_none, self.wr_lb_xgd2,
                   self.wr_lb_xgd3, self.wr_lb_dvd,
                   self.wr_lb_custom):
            self.wr_lb_group.addButton(rb)
            lbl.addWidget(rb)
        crow = QHBoxLayout()
        self.wr_lb_value = QLineEdit()
        self.wr_lb_value.setPlaceholderText(
            "Sector count, or the .dvd value once read")
        crow.addWidget(self.wr_lb_value, 1)
        lbl.addLayout(crow)
        self.wr_lb_note = QLabel(
            "Presets burn through growisofs' "
            "-use-the-force-luke=break flag and are "
            "BENCH-PROOF-PENDING: the gate closes when a disc "
            "burned on a real bench drive reads its layer break "
            "back as exactly the requested value.")
        self.wr_lb_note.setWordWrap(True)
        lbl.addWidget(self.wr_lb_note)
        lay.addWidget(lb_box)

        orow = QHBoxLayout()
        self.wr_test = QCheckBox("Test mode (simulate, no burn)")
        self.wr_verify = QCheckBox("Verify after write")
        self.wr_eject = QCheckBox("Eject when finished")
        for w in (self.wr_test, self.wr_verify, self.wr_eject):
            orow.addWidget(w)
        orow.addStretch(1)
        lay.addLayout(orow)
        note = QLabel(
            "Xbox 360 workflow: check the ISO with the ABGX360 "
            "GUI first, then burn it here, then run Verify.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.wr_add = QPushButton("Add Write Job to Queue")
        lay.addWidget(self.wr_add)
        lay.addStretch(1)
        return page

    # ----------------------------------------------------------
    # Build tab
    # ----------------------------------------------------------
    def _tab_build(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("Source files and folders:"))
        self.bd_sources = QListWidget()
        lay.addWidget(self.bd_sources)
        srow = QHBoxLayout()
        self.bd_add_files = QPushButton("Add Files...")
        self.bd_add_folder = QPushButton("Add Folder...")
        self.bd_add_files_net = QPushButton("Add Files (Share)...")
        self.bd_add_folder_net = QPushButton(
            "Add Folder (Share)...")
        self.bd_remove = QPushButton("Remove Selected")
        for b in (self.bd_add_files, self.bd_add_folder,
                  self.bd_add_files_net, self.bd_add_folder_net,
                  self.bd_remove):
            srow.addWidget(b)
        srow.addStretch(1)
        lay.addLayout(srow)
        orow = QHBoxLayout()
        self.bd_out_group = QButtonGroup(self)
        self.bd_out_iso = QRadioButton("To ISO file")
        self.bd_out_disc = QRadioButton("Straight to disc")
        self.bd_out_iso.setChecked(True)
        self.bd_out_group.addButton(self.bd_out_iso)
        self.bd_out_group.addButton(self.bd_out_disc)
        orow.addWidget(self.bd_out_iso)
        orow.addWidget(self.bd_out_disc)
        orow.addStretch(1)
        lay.addLayout(orow)
        self.bd_iso, self.bd_iso_btn, self.bd_iso_net = self._path_row(
            lay, "Output ISO:")
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Destination drive:"))
        self.bd_device = self._device_combo()
        drow.addWidget(self.bd_device, 1)
        lay.addLayout(drow)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Volume label:"))
        self.bd_label = QLineEdit("DISC_TOOLS")
        lrow.addWidget(self.bd_label, 1)
        lay.addLayout(lrow)
        frow = QHBoxLayout()
        self.bd_joliet = QCheckBox("Joliet")
        self.bd_joliet.setChecked(True)
        self.bd_rockridge = QCheckBox("Rock Ridge")
        self.bd_rockridge.setChecked(True)
        self.bd_udf = QCheckBox("UDF")
        for w in (self.bd_joliet, self.bd_rockridge, self.bd_udf):
            frow.addWidget(w)
        frow.addStretch(1)
        lay.addLayout(frow)
        brow = QHBoxLayout()
        self.bd_bootable = QCheckBox("Bootable (El Torito)")
        brow.addWidget(self.bd_bootable)
        brow.addStretch(1)
        lay.addLayout(brow)
        self.bd_boot, self.bd_boot_btn, self.bd_boot_net = self._path_row(
            lay, "Boot image:",
            "Must be a file inside one of the source folders")
        note = QLabel(
            "DVD-Video authoring is out of scope for v1: "
            "xorriso cannot author DVD-Video-spec UDF.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.bd_add = QPushButton("Add Build Job to Queue")
        lay.addWidget(self.bd_add)
        lay.addStretch(1)
        return page

    # ----------------------------------------------------------
    # Read tab
    # ----------------------------------------------------------
    def _tab_read(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Source drive:"))
        self.rd_device = self._device_combo()
        drow.addWidget(self.rd_device, 1)
        lay.addLayout(drow)
        self.rd_sub_group = QButtonGroup(self)
        self.rd_sub_iso = QRadioButton("ISO image (data disc)")
        self.rd_sub_bincue = QRadioButton("BIN/CUE (CD)")
        self.rd_sub_pres = QRadioButton(
            "Preservation dump (redumper)")
        self.rd_sub_dmg = QRadioButton(
            "Damaged disc (ddrescue, resumable)")
        self.rd_sub_iso.setChecked(True)
        for rb in (self.rd_sub_iso, self.rd_sub_bincue,
                   self.rd_sub_pres, self.rd_sub_dmg):
            self.rd_sub_group.addButton(rb)
            lay.addWidget(rb)
        self.rd_stack = QStackedWidget()
        # ISO page
        p = QWidget()
        pl = QVBoxLayout(p)
        self.rd_iso_out, self.rd_iso_btn, self.rd_iso_net = self._path_row(
            pl, "Output ISO:")
        pl.addStretch(1)
        self.rd_stack.addWidget(p)
        # BIN/CUE page
        p = QWidget()
        pl = QVBoxLayout(p)
        self.rd_bin_base, self.rd_bin_btn, self.rd_bin_net = self._path_row(
            pl, "Output name (no extension):",
            "Produces <name>.bin and <name>.cue")
        pl.addStretch(1)
        self.rd_stack.addWidget(p)
        # Preservation page
        p = QWidget()
        pl = QVBoxLayout(p)
        self.rd_pres_dir, self.rd_pres_dir_btn, self.rd_pres_dir_net = self._path_row(
            pl, "Output folder:")
        nrow = QHBoxLayout()
        nrow.addWidget(QLabel("Image name:"))
        self.rd_pres_name = QLineEdit()
        nrow.addWidget(self.rd_pres_name, 1)
        nrow.addWidget(QLabel("Retries:"))
        self.rd_pres_retries = QSpinBox()
        self.rd_pres_retries.setRange(0, 100)
        self.rd_pres_retries.setValue(20)
        nrow.addWidget(self.rd_pres_retries)
        pl.addLayout(nrow)
        self.rd_pres_dat, self.rd_pres_dat_btn, self.rd_pres_dat_net = self._path_row(
            pl, "Redump DAT (optional):",
            "Verdict the dump against a Redump DAT file")
        pl.addStretch(1)
        self.rd_stack.addWidget(p)
        # Damaged page
        p = QWidget()
        pl = QVBoxLayout(p)
        self.rd_dmg_out, self.rd_dmg_btn, self.rd_dmg_net = self._path_row(
            pl, "Output ISO:")
        self.rd_dmg_map, self.rd_dmg_map_btn, self.rd_dmg_map_net = self._path_row(
            pl, "Mapfile:",
            "Defaults to the output name + .map -- re-running "
            "the same job resumes")
        pl.addStretch(1)
        self.rd_stack.addWidget(p)
        lay.addWidget(self.rd_stack)
        self.rd_add = QPushButton("Add Read Job to Queue")
        lay.addWidget(self.rd_add)
        lay.addStretch(1)
        return page

    # ----------------------------------------------------------
    # Verify tab
    # ----------------------------------------------------------
    def _tab_verify(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        self.vf_image, self.vf_image_btn, self.vf_image_net = self._path_row(
            lay, "Image file:")
        trow = QHBoxLayout()
        trow.addWidget(QLabel("Compare against:"))
        self.vf_device = self._device_combo()
        trow.addWidget(self.vf_device, 1)
        self.vf_file_btn = QPushButton("File...")
        trow.addWidget(self.vf_file_btn)
        self.vf_file_net = QPushButton("Share...")
        trow.addWidget(self.vf_file_net)
        lay.addLayout(trow)
        note = QLabel(
            "Sector-by-sector compare of the image against the "
            "disc (or another file), plus MD5 / SHA1 / SHA256 "
            "of the image.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.vf_add = QPushButton("Add Verify Job to Queue")
        lay.addWidget(self.vf_add)
        lay.addStretch(1)
        return page

    # ----------------------------------------------------------
    # Discovery tab
    # ----------------------------------------------------------
    def _tab_discovery(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        srow = QHBoxLayout()
        self.dv_scan = QPushButton("Scan Drives")
        srow.addWidget(self.dv_scan)
        srow.addStretch(1)
        lay.addLayout(srow)
        self.dv_table = QTableWidget(0, 5)
        self.dv_table.setHorizontalHeaderLabels(
            ["Device", "Drive", "Media", "Status",
             "Capacity (sectors, as reported)"])
        self.dv_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self.dv_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        lay.addWidget(self.dv_table)
        self.dv_note = QLabel("")
        self.dv_note.setWordWrap(True)
        lay.addWidget(self.dv_note)
        lay.addStretch(1)
        return page

    # ----------------------------------------------------------
    # Wiring
    # ----------------------------------------------------------
    def _wire(self):
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear_queue)
        self.btn_run.clicked.connect(self._run_queue)
        self.btn_stop.clicked.connect(self._stop_queue)
        self.table.itemSelectionChanged.connect(
            self._show_selected)
        self.tabs.currentChanged.connect(
            lambda _i: self._update_preview())
        # Write
        self.wr_image_btn.clicked.connect(
            lambda: self._browse_into(
                self.wr_image, "image"))
        self.wr_image_net.clicked.connect(
            lambda: self._browse_share(
                self.wr_image, "image"))
        self.wr_image.textChanged.connect(self._write_image_changed)
        self.wr_add.clicked.connect(self._add_write)
        # Build
        self.bd_add_files.clicked.connect(self._build_add_files)
        self.bd_add_folder.clicked.connect(self._build_add_folder)
        self.bd_add_files_net.clicked.connect(
            self._build_add_files_share)
        self.bd_add_folder_net.clicked.connect(
            self._build_add_folder_share)
        self.bd_remove.clicked.connect(
            lambda: [self.bd_sources.takeItem(i.row())
                     for i in self.bd_sources.selectedIndexes()])
        self.bd_iso_btn.clicked.connect(
            lambda: self._browse_save(self.bd_iso, "ISO image",
                                      "Images (*.iso)"))
        self.bd_iso_net.clicked.connect(
            lambda: self._browse_share_save(
                self.bd_iso, "ISO image", "Images (*.iso)"))
        self.bd_boot_btn.clicked.connect(
            lambda: self._browse_into(self.bd_boot, "file"))
        self.bd_boot_net.clicked.connect(
            lambda: self._browse_share(self.bd_boot, "file"))
        self.bd_add.clicked.connect(self._add_build)
        # Read
        for rb, idx in ((self.rd_sub_iso, 0),
                        (self.rd_sub_bincue, 1),
                        (self.rd_sub_pres, 2),
                        (self.rd_sub_dmg, 3)):
            rb.toggled.connect(
                lambda on, i=idx: on and self.rd_stack.setCurrentIndex(i))
        self.rd_iso_btn.clicked.connect(
            lambda: self._browse_save(self.rd_iso_out, "ISO image",
                                      "Images (*.iso)"))
        self.rd_iso_net.clicked.connect(
            lambda: self._browse_share_save(
                self.rd_iso_out, "ISO image", "Images (*.iso)"))
        self.rd_bin_btn.clicked.connect(
            lambda: self._browse_save(self.rd_bin_base, "BIN base",
                                      "", strip_ext=True))
        self.rd_bin_net.clicked.connect(
            lambda: self._browse_share_save(
                self.rd_bin_base, "BIN base", "", strip_ext=True))
        self.rd_pres_dir_btn.clicked.connect(
            lambda: self._browse_dir(self.rd_pres_dir))
        self.rd_pres_dir_net.clicked.connect(
            lambda: self._browse_share_dir(self.rd_pres_dir))
        self.rd_pres_dat_btn.clicked.connect(
            lambda: self._browse_into(self.rd_pres_dat, "file"))
        self.rd_pres_dat_net.clicked.connect(
            lambda: self._browse_share(self.rd_pres_dat, "file"))
        self.rd_dmg_btn.clicked.connect(
            lambda: self._browse_save(self.rd_dmg_out, "ISO image",
                                      "Images (*.iso)"))
        self.rd_dmg_net.clicked.connect(
            lambda: self._browse_share_save(
                self.rd_dmg_out, "ISO image", "Images (*.iso)"))
        self.rd_dmg_map_btn.clicked.connect(
            lambda: self._browse_save(self.rd_dmg_map, "Mapfile",
                                      ""))
        self.rd_dmg_map_net.clicked.connect(
            lambda: self._browse_share_save(
                self.rd_dmg_map, "Mapfile", ""))
        self.rd_add.clicked.connect(self._add_read)
        # Verify
        self.vf_image_btn.clicked.connect(
            lambda: self._browse_into(self.vf_image, "image"))
        self.vf_image_net.clicked.connect(
            lambda: self._browse_share(
                self.vf_image, "image"))
        self.vf_file_btn.clicked.connect(
            lambda: self._browse_into(self.vf_device, "file"))
        self.vf_file_net.clicked.connect(
            lambda: self._browse_share(self.vf_device, "file"))
        self.vf_add.clicked.connect(self._add_verify)
        # Discovery
        self.dv_scan.clicked.connect(self._scan_drives)
        # Live preview: any form edit refreshes the command line
        for w in self.findChildren(QLineEdit):
            w.textChanged.connect(self._update_preview)
        for w in self.findChildren(QCheckBox):
            w.toggled.connect(lambda _on: self._update_preview())
        for w in self.findChildren(QRadioButton):
            w.toggled.connect(lambda _on: self._update_preview())
        for w in self.findChildren(QComboBox):
            w.currentTextChanged.connect(
                lambda _t: self._update_preview())
        for w in self.findChildren(QSpinBox):
            w.valueChanged.connect(
                lambda _v: self._update_preview())
        self.bd_sources.model().rowsInserted.connect(
            lambda *_a: self._update_preview())
        self.bd_sources.model().rowsRemoved.connect(
            lambda *_a: self._update_preview())

    # ----------------------------------------------------------
    # File-dialog helpers (local + network share)
    # ----------------------------------------------------------
    _IMAGE_FILTER = ("Disc images (*.iso *.cue *.bin *.img "
                     "*.zip *.7z);;Archives (*.zip *.7z);;"
                     "All files (*)")

    @staticmethod
    def _start_dir(edit):
        """Reopen dialogs where the field already points."""
        text = edit.text().strip() if edit else ""
        if not text:
            return ""
        p = Path(text)
        if p.is_dir():
            return str(p)
        parent = str(p.parent)
        return parent if parent not in ("", ".") else ""

    def _browse_into(self, edit, kind):
        if kind == "image":
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose an image file",
                self._start_dir(edit), self._IMAGE_FILTER)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose a file", self._start_dir(edit))
        if path:
            edit.setText(path)

    def _browse_save(self, edit, title, filt, strip_ext=False):
        path, _ = QFileDialog.getSaveFileName(
            self, title, self._start_dir(edit), filt)
        if path:
            if strip_ext:
                path = str(Path(path).with_suffix(""))
            edit.setText(path)

    def _browse_dir(self, edit):
        path = QFileDialog.getExistingDirectory(
            self, "Choose a folder", self._start_dir(edit))
        if path:
            edit.setText(path)

    def _build_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add files")
        for p in paths:
            self.bd_sources.addItem(p)

    def _build_add_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "Add folder")
        if path:
            self.bd_sources.addItem(path)

    # --- network share flow -----------------------------------
    def _net_data(self):
        net = getattr(self, "_net", None)
        if not isinstance(net, dict):
            net = {"servers": [], "shares": {}, "recent": []}
            self._net = net
        return net

    def _remember_share(self, server, share):
        net = self._net_data()
        servers = net.setdefault("servers", [])
        if server in servers:
            servers.remove(server)
        servers.insert(0, server)
        shares = net.setdefault("shares", {})
        lst = shares.setdefault(server, [])
        if share in lst:
            lst.remove(share)
        lst.insert(0, share)
        recent = net.setdefault("recent", [])
        ref = server + "/" + share
        if ref in recent:
            recent.remove(ref)
        recent.insert(0, ref)
        net["servers"] = servers[:10]
        net["shares"] = {k: v[:10] for k, v in shares.items()}
        net["recent"] = recent[:10]

    def _ask_share(self):
        """Share dialog -> resolve (mount) -> local root path,
        or None when cancelled / failed (error shown here)."""
        dlg = ShareDialog(self, self._net_data())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        vals = dlg.values()
        if not vals["server"] or not vals["share"]:
            self._warn("A share needs both a server and a "
                       "share name.")
            return None
        self._remember_share(vals["server"], vals["share"])
        root = self._resolve_share(vals)
        if root is None:
            return None
        if vals["subpath"]:
            root = str(Path(root) / vals["subpath"])
        return root

    def _run_blocking(self, fn, stage_text):
        """Run fn(log) on a worker (mounting a share or
        extracting an archive can take a while); the wait
        cursor + event pump keep the window responsive.
        Returns fn's result dict."""
        holder = {}
        worker = Worker(fn)
        self._workers.add(worker)
        worker.log.connect(
            lambda s: self.raw_log.appendPlainText(s))
        worker.done.connect(lambda r: holder.update(r))
        self._set_stage(stage_text)
        QApplication.setOverrideCursor(
            Qt.CursorShape.WaitCursor)
        worker.start()
        while not holder:
            QApplication.processEvents()
            threading.Event().wait(0.05)
        QApplication.restoreOverrideCursor()
        self._workers.discard(worker)
        return holder

    def _resolve_share(self, vals):
        """Mount the share; returns its local path or None
        (the error is shown here)."""
        def fn(log):
            try:
                return {"path": engine.resolve_share(
                    vals["server"], vals["share"], log=log,
                    user=vals["user"],
                    password=vals["password"])}
            except engine.EngineError as e:
                return {"error": str(e)}

        holder = self._run_blocking(
            fn, "Mounting smb://%s/%s ..."
            % (vals["server"], vals["share"]))
        if holder.get("error"):
            self._set_stage("Share mount failed.")
            QMessageBox.warning(self, "Network share",
                                holder["error"])
            return None
        self._set_stage("Share ready: " + holder["path"])
        return holder["path"]

    def _resolve_archive_job(self, job):
        """If the job's image is a .zip/.7z, extract it to
        local scratch and point the job at the extracted
        image (the archive itself is never burned/verified
        in place, and is left untouched). Returns the job,
        or None when extraction failed (error shown here)."""
        image = job.get("image", "")
        if not engine.is_archive_path(image):
            return job

        def fn(log):
            try:
                return {"path": engine.extract_archive(
                    image, log)}
            except engine.EngineError as e:
                return {"error": str(e)}

        holder = self._run_blocking(
            fn, "Extracting %s ..." % Path(image).name)
        if holder.get("error"):
            self._set_stage("Archive extraction failed.")
            QMessageBox.warning(self, "Archive input",
                                holder["error"])
            return None
        job = dict(job)
        job["source_archive"] = image
        job["image"] = holder["path"]
        self._set_stage("Archive image: " + holder["path"])
        return job

    def _browse_share(self, edit, kind):
        root = self._ask_share()
        if not root:
            return
        if kind == "image":
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose an image file", root,
                self._IMAGE_FILTER)
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose a file", root)
        if path:
            edit.setText(path)

    def _browse_share_save(self, edit, title, filt,
                           strip_ext=False):
        root = self._ask_share()
        if not root:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, title, root, filt)
        if path:
            if strip_ext:
                path = str(Path(path).with_suffix(""))
            edit.setText(path)

    def _browse_share_dir(self, edit):
        root = self._ask_share()
        if not root:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Choose a folder", root)
        if path:
            edit.setText(path)

    def _build_add_files_share(self):
        root = self._ask_share()
        if not root:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add files", root)
        for p in paths:
            self.bd_sources.addItem(p)

    def _build_add_folder_share(self):
        root = self._ask_share()
        if not root:
            return
        path = QFileDialog.getExistingDirectory(
            self, "Add folder", root)
        if path:
            self.bd_sources.addItem(path)

    def _write_image_changed(self, text):
        if engine.is_archive_path(text.strip()):
            self._set_stage(
                "Archive -- extracted to local scratch when "
                "the Write job is added.")
            self._update_preview()
            return
        lb = engine.dvd_for_image(text.strip())
        if lb is not None:
            self.wr_lb_value.setText(str(lb))
            if not self.wr_lb_custom.isChecked():
                self.wr_lb_dvd.setChecked(True)
            self._set_stage(
                ".dvd sidecar found -- layer break %d." % lb)
        self._update_preview()

    # ----------------------------------------------------------
    # Collecting jobs from the forms
    # ----------------------------------------------------------
    def _warn(self, text):
        QMessageBox.warning(self, "Not ready", text)

    def _collect_write(self, quiet=False):
        image = self.wr_image.text().strip()
        device = self.wr_device.currentText().strip()
        if not image:
            if not quiet:
                self._warn("Pick an image file first.")
            return None
        if not device:
            if not quiet:
                self._warn("Pick a destination drive (Discovery "
                           "lists them; you can also type a "
                           "device path).")
            return None
        lb = None
        if self.wr_lb_xgd2.isChecked():
            lb = engine.XGD2_BREAK
        elif self.wr_lb_xgd3.isChecked():
            lb = engine.XGD3_BREAK
        elif self.wr_lb_dvd.isChecked() \
                or self.wr_lb_custom.isChecked():
            raw = self.wr_lb_value.text().strip()
            if not raw.isdigit():
                if not quiet:
                    self._warn("The layer break needs a sector "
                               "count (pick an image with a .dvd "
                               "sidecar, a preset, or type one).")
                return None
            lb = int(raw)
        return {"kind": "write", "image": image, "device": device,
                "engine": self.wr_engine.currentText(),
                "speed": self.wr_speed.currentText(),
                "layerbreak": lb,
                "test": self.wr_test.isChecked(),
                "verify_after": self.wr_verify.isChecked(),
                "eject": self.wr_eject.isChecked()}

    def _collect_build(self, quiet=False):
        sources = [self.bd_sources.item(i).text()
                   for i in range(self.bd_sources.count())]
        if not sources:
            if not quiet:
                self._warn("Add at least one file or folder.")
            return None
        job = {"kind": "build", "sources": sources,
               "label": self.bd_label.text().strip()
               or "DISC_TOOLS",
               "joliet": self.bd_joliet.isChecked(),
               "rockridge": self.bd_rockridge.isChecked(),
               "udf": self.bd_udf.isChecked(),
               "bootable": self.bd_bootable.isChecked(),
               "boot_image": self.bd_boot.text().strip()}
        if self.bd_out_iso.isChecked():
            job["output"] = "iso"
            job["iso_path"] = self.bd_iso.text().strip()
            if not job["iso_path"]:
                if not quiet:
                    self._warn("Choose an output ISO path.")
                return None
        else:
            job["output"] = "disc"
            job["device"] = self.bd_device.currentText().strip()
            if not job["device"]:
                if not quiet:
                    self._warn("Pick a destination drive.")
                return None
        return job

    def _collect_read(self, quiet=False):
        device = self.rd_device.currentText().strip()
        if not device:
            if not quiet:
                self._warn("Pick a source drive.")
            return None
        job = {"kind": "read", "device": device}
        if self.rd_sub_iso.isChecked():
            job["sub"] = "iso"
            job["output"] = self.rd_iso_out.text().strip()
            if not job["output"]:
                if not quiet:
                    self._warn("Choose an output ISO path.")
                return None
        elif self.rd_sub_bincue.isChecked():
            job["sub"] = "bincue"
            job["output_base"] = self.rd_bin_base.text().strip()
            if not job["output_base"]:
                if not quiet:
                    self._warn("Choose an output name.")
                return None
        elif self.rd_sub_pres.isChecked():
            job["sub"] = "preservation"
            job["output_dir"] = self.rd_pres_dir.text().strip()
            job["image_name"] = self.rd_pres_name.text().strip()
            job["retries"] = self.rd_pres_retries.value()
            job["dat_path"] = self.rd_pres_dat.text().strip()
            if not job["output_dir"] or not job["image_name"]:
                if not quiet:
                    self._warn("Preservation dumps need an "
                               "output folder and an image name.")
                return None
        else:
            job["sub"] = "damaged"
            job["output"] = self.rd_dmg_out.text().strip()
            job["mapfile"] = self.rd_dmg_map.text().strip() \
                or (job["output"] + ".map" if job["output"]
                    else "")
            if not job["output"]:
                if not quiet:
                    self._warn("Choose an output ISO path.")
                return None
        return job

    def _collect_verify(self, quiet=False):
        image = self.vf_image.text().strip()
        device = self.vf_device.currentText().strip()
        if not image or not device:
            if not quiet:
                self._warn("Verify needs an image file and a "
                           "device (or file) to compare against.")
            return None
        return {"kind": "verify", "image": image,
                "device": device}

    def _collect_current(self, quiet=True):
        idx = self.tabs.currentIndex()
        fn = (self._collect_write, self._collect_build,
              self._collect_read, self._collect_verify,
              lambda quiet=True: None)[idx]
        try:
            return fn(quiet=quiet)
        except Exception:  # noqa: BLE001
            return None

    def _update_preview(self):
        idx = self.tabs.currentIndex()
        edit = (self.wr_image, None, None,
                self.vf_image, None)[idx]
        if edit is not None and engine.is_archive_path(
                edit.text().strip()):
            self.preview.setText(
                "(archive -- extracted to local scratch when "
                "the job is added)")
            return
        job = self._collect_current(quiet=True)
        if job is None:
            self.preview.setText(
                "(fill in the form to see the engine command)")
        else:
            self.preview.setText(engine.preview_command(job))

    # ----------------------------------------------------------
    # Queue handling
    # ----------------------------------------------------------
    def _add_job(self, job):
        item = {"job": job, "name": engine.job_name(job),
                "status": "Queued", "result": "",
                "results": [], "raw": ""}
        self.items.append(item)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._refresh_row(row)
        return item

    def _add_write(self):
        job = self._collect_write()
        if job is None:
            return
        job = self._resolve_archive_job(job)
        if job is None:
            return
        for _ in range(self.wr_copies.value()):
            self._add_job(dict(job))
        self._set_stage("Added: " + engine.job_name(job))

    def _add_build(self):
        job = self._collect_build()
        if job is not None:
            self._add_job(job)
            self._set_stage("Added: " + engine.job_name(job))

    def _add_read(self):
        job = self._collect_read()
        if job is not None:
            self._add_job(job)
            self._set_stage("Added: " + engine.job_name(job))

    def _add_verify(self):
        job = self._collect_verify()
        if job is None:
            return
        job = self._resolve_archive_job(job)
        if job is not None:
            self._add_job(job)
            self._set_stage("Added: " + engine.job_name(job))

    def _refresh_row(self, row):
        item = self.items[row]
        job = item["job"]
        detail = ""
        if job["kind"] == "write":
            detail = job["image"]
            if job.get("layerbreak"):
                detail += "  [layer break %d]" % job["layerbreak"]
        elif job["kind"] == "build":
            detail = job.get("iso_path") or job.get("device", "")
        elif job["kind"] == "read":
            detail = (job.get("output") or job.get("output_base")
                      or job.get("image_name", ""))
        elif job["kind"] == "verify":
            detail = job["image"]
        if job.get("source_archive"):
            detail += "  [from %s]" % Path(
                job["source_archive"]).name
        vals = [item["name"], item["status"], item["result"],
                detail]
        for col, val in enumerate(vals):
            cell = self.table.item(row, col)
            if cell is None:
                cell = QTableWidgetItem()
                self.table.setItem(row, col, cell)
            cell.setText(val)

    def _remove_selected(self):
        rows = sorted({i.row() for i in
                       self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self.items[r]

    def _clear_queue(self):
        if self._running:
            return
        self.items = []
        self.table.setRowCount(0)
        self.results.setRowCount(0)
        self.raw_log.clear()

    def _selected_index(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _show_selected(self):
        idx = self._selected_index()
        if 0 <= idx < len(self.items):
            self._show_item(idx)

    def _show_item(self, idx):
        item = self.items[idx]
        self.results.setRowCount(0)
        for r in item["results"]:
            row = self.results.rowCount()
            self.results.insertRow(row)
            self.results.setItem(row, 0,
                                 QTableWidgetItem(r["check"]))
            self.results.setItem(row, 1,
                                 QTableWidgetItem(r["result"]))
            self.results.setItem(row, 2,
                                 QTableWidgetItem(r["detail"]))
        self.raw_log.setPlainText(item["raw"])

    # ----------------------------------------------------------
    # Settings persistence (form values + window geometry)
    # ----------------------------------------------------------
    def _save_settings(self):
        data = {
            "write": {"image": self.wr_image.text(),
                      "device": self.wr_device.currentText(),
                      "engine": self.wr_engine.currentText(),
                      "speed": self.wr_speed.currentText()},
            "build": {"iso": self.bd_iso.text(),
                      "device": self.bd_device.currentText(),
                      "label": self.bd_label.text()},
            "read": {"device": self.rd_device.currentText()},
            "verify": {"image": self.vf_image.text(),
                       "device": self.vf_device.currentText()},
            "network": self._net_data(),
            "geometry": self.saveGeometry().toHex().data().decode(),
        }
        try:
            engine.DATA_DIR.mkdir(parents=True, exist_ok=True)
            engine.SETTINGS_PATH.write_text(json.dumps(data,
                                                       indent=1))
        except Exception:  # noqa: BLE001
            pass

    def _load_settings(self):
        try:
            data = json.loads(engine.SETTINGS_PATH.read_text())
        except Exception:  # noqa: BLE001
            return
        w = data.get("write", {})
        self.wr_image.setText(w.get("image", ""))
        if w.get("device"):
            self.wr_device.setCurrentText(w["device"])
        if w.get("engine"):
            self.wr_engine.setCurrentText(w["engine"])
        if w.get("speed"):
            self.wr_speed.setCurrentText(w["speed"])
        b = data.get("build", {})
        self.bd_iso.setText(b.get("iso", ""))
        if b.get("device"):
            self.bd_device.setCurrentText(b["device"])
        if b.get("label"):
            self.bd_label.setText(b["label"])
        r = data.get("read", {})
        if r.get("device"):
            self.rd_device.setCurrentText(r["device"])
        v = data.get("verify", {})
        self.vf_image.setText(v.get("image", ""))
        if v.get("device"):
            self.vf_device.setCurrentText(v["device"])
        net = data.get("network")
        if isinstance(net, dict):
            self._net = {"servers": net.get("servers", []),
                         "shares": net.get("shares", {}),
                         "recent": net.get("recent", [])}
        geo = data.get("geometry")
        if geo:
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(
                    QByteArray.fromHex(geo.encode()))
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, event):  # noqa: N802 (Qt name)
        self._save_settings()
        super().closeEvent(event)

    # ----------------------------------------------------------
    # First-run setup
    # ----------------------------------------------------------
    def _set_stage(self, text):
        self.stage_label.setText(text)

    def _offer_dep_install(self, info):
        """Ask what to do about missing engine packages; returns
        "install" or "manual". Called only on the GUI thread.
        Under the smoke driver the offer is recorded and the
        choice comes from the smoke config instead of a dialog."""
        self._dep_offers.append(dict(info))
        cfg_path = os.environ.get("DISC_TOOLS_SMOKE")
        if cfg_path:
            try:
                cfg = json.loads(Path(cfg_path).read_text())
            except Exception:  # noqa: BLE001
                cfg = {}
            return cfg.get("dep_choice", "manual")
        names = "\n".join("  - " + n for n in info["missing"])
        if info["family"] != "debian":
            QMessageBox.information(
                self, "Engine packages missing",
                "Disc Tools needs the following, which are not "
                "installed:\n" + names
                + "\n\nAutomatic install is only available on "
                "Debian/Ubuntu-family systems. On this system, "
                "install them with:\n" + info["manual_command"]
                + "\n\nThen use Help > Re-run first-run setup.")
            return "manual"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Engine packages missing")
        box.setText(
            "Disc Tools needs the following, which are not "
            "installed:\n" + names
            + "\n\nPackages: " + ", ".join(info["packages"])
            + "\n\nInstall them automatically now? You will be "
            "asked for your password once.")
        btn_install = box.addButton(
            "Install automatically",
            QMessageBox.ButtonRole.AcceptRole)
        box.addButton("I'll install it myself",
                      QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return "install" if box.clickedButton() is btn_install \
            else "manual"

    def _stop_setup_for_deps(self, info):
        """The user chose to install the packages themselves (or
        this distro has no automatic path): stop setup cleanly --
        not an error state -- with the exact command in the log."""
        self._setup_stopped = True
        self._set_stage(
            "Setup stopped -- install the engine packages, then "
            "use Help > Re-run first-run setup.")
        self.raw_log.appendPlainText("SETUP: " + info["manual_text"])

    def _start_setup(self, install_deps=False):
        if engine.engines_ready():
            self._setup_done = True
            self._set_stage("Ready -- all engines found.")
            return
        self._setup_error = None
        self._setup_stopped = False
        if not install_deps:
            self._dep_offer_rounds = 0
            # Pre-flight: missing engine packages are an offer to
            # install, never a dead-end later.
            missing = engine.missing_deps()
            if missing:
                info = engine.dep_info(missing)
                if self._offer_dep_install(info) == "install":
                    install_deps = True
                else:
                    self._stop_setup_for_deps(info)
                    return
        self._set_stage("First-run setup: starting...")
        self.btn_run.setEnabled(False)

        def job(log):
            def stage(msg):
                self._stage_queue.append(msg)
            try:
                engine.ensure_engines(log, stage,
                                      install_deps=install_deps)
                return {"ok": True}
            except engine.DepsMissingError as e:
                return {"deps_missing": e.info}
            except engine.EngineError as e:
                return {"error": str(e)}

        self._stage_queue = []
        worker = Worker(job)
        self._workers.add(worker)
        worker.log.connect(
            lambda s: self.raw_log.appendPlainText(s))
        worker.done.connect(
            lambda r: self._setup_finished(r, worker))
        worker.start()
        self._setup_timer = QTimer(self)
        self._setup_timer.timeout.connect(self._drain_stage_queue)
        self._setup_timer.start(200)

    def _drain_stage_queue(self):
        while getattr(self, "_stage_queue", None):
            self._set_stage(self._stage_queue.pop(0))

    def _setup_finished(self, result, worker):
        self._workers.discard(worker)
        if hasattr(self, "_setup_timer"):
            self._setup_timer.stop()
        self._drain_stage_queue()
        if result.get("deps_missing"):
            # The failure path gets one offer too; a second
            # arrival falls through instead of looping the dialog.
            self._dep_offer_rounds += 1
            if self._dep_offer_rounds <= 1:
                info = result["deps_missing"]
                if self._offer_dep_install(info) == "install":
                    self._start_setup(install_deps=True)
                else:
                    self._stop_setup_for_deps(info)
                    self.btn_run.setEnabled(True)
                return
        self.btn_run.setEnabled(True)
        if result.get("ok"):
            self._setup_done = True
            self._set_stage("Ready -- engines installed.")
        else:
            self._setup_error = result.get("error") or (
                result["deps_missing"]["manual_text"]
                if result.get("deps_missing") else "setup failed")
            self._set_stage(
                "Setup did not complete -- see the log. "
                "Run Queue is unavailable until setup finishes.")
            self.raw_log.appendPlainText(
                "SETUP: " + self._setup_error)

    # ----------------------------------------------------------
    # Discovery
    # ----------------------------------------------------------
    def _scan_drives(self):
        def job(_log):
            return {"drives": engine.discover_drives()}
        worker = Worker(job)
        self._workers.add(worker)
        worker.done.connect(
            lambda r: self._scan_finished(r, worker))
        worker.start()

    def _scan_finished(self, result, worker):
        self._workers.discard(worker)
        self._scan_done = True
        drives = result.get("drives") or []
        self._drives = drives
        self.dv_table.setRowCount(0)
        for info in drives:
            row = self.dv_table.rowCount()
            self.dv_table.insertRow(row)
            name = (info["vendor"] + " " + info["model"]).strip()
            if info["revision"]:
                name += " (" + info["revision"] + ")"
            cap = info["capacity_sectors"]
            vals = [info["device"], name, info["media"],
                    info["media_status"],
                    str(cap) if cap is not None else "--"]
            for col, val in enumerate(vals):
                self.dv_table.setItem(row, col,
                                      QTableWidgetItem(val))
        if not drives:
            row = self.dv_table.rowCount()
            self.dv_table.insertRow(row)
            self.dv_table.setItem(
                row, 0, QTableWidgetItem(
                    "No optical drives found on this system."))
        # Fill the device combos, preserving what the user typed.
        devices = [d["device"] for d in drives]
        for combo in (self.wr_device, self.bd_device,
                      self.rd_device, self.vf_device):
            current = combo.currentText()
            combo.clear()
            combo.addItems(devices)
            if current:
                combo.setCurrentText(current)
            elif devices:
                combo.setCurrentText(devices[0])
        self.dv_note.setText(
            "Capacity is what the drive itself reports -- this "
            "app never imposes an artificial image-size cap, so "
            "a BurnerMax-flashed drive's full XGD3 capacity is "
            "honoured as-is. Flashed drives work from v1. The "
            "stock-drive BurnerMax payload unlock is phase 2 "
            "and is NOT in this build.\n\n"
            "Xbox 360 workflow: check with the ABGX360 GUI "
            "first, then burn (Write tab), then Verify.")

    # ----------------------------------------------------------
    # Running the queue
    # ----------------------------------------------------------
    def _run_queue(self):
        if self._running:
            return
        if not self._setup_done and not engine.engines_ready():
            QMessageBox.warning(
                self, "Engines not ready",
                "The disc engines are not set up yet.\n\n"
                + (self._setup_error or
                   "First-run setup has not completed."))
            return
        if not self.items:
            QMessageBox.information(
                self, "Nothing to run", "Add at least one job.")
            return
        self._running = True
        self._stop = False
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setMaximum(len(self.items))
        self.progress_bar.setValue(0)
        threading.Thread(target=self._run_all, daemon=True).start()

    def _stop_queue(self):
        self._stop = True
        proc = self._control.get("proc")
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _run_all(self, ):
        # Runs on a plain thread; all widget access goes through
        # the _ui queue drained by a QTimer on the GUI thread.
        work = list(self.items)
        done = 0
        idx = 0
        while idx < len(work):
            if self._stop:
                break
            item = work[idx]
            job = item["job"]
            row = next(i for i, it in enumerate(self.items)
                       if it is item)
            self._invoke(lambda r=row: self._item_started(r))
            raw_lines = []

            def log(line, _buf=raw_lines):
                _buf.append(line + "\n")
                if line.startswith("+ "):
                    self._invoke(
                        lambda t=line[2:90]:
                        self._set_stage("Running: " + t))

            try:
                rc, results = engine.run_job(job, log,
                                             self._control)
            except Exception as e:  # noqa: BLE001
                rc, results = -1, [{
                    "check": "Job", "result": "FAIL",
                    "detail": "GUI error: %s" % e}]
                raw_lines.append("GUI error: %s\n" % e)
            raw = "".join(raw_lines)
            verdict = self._verdict(results, rc)
            self._invoke(lambda r=row, rw=raw, res=results,
                         v=verdict:
                         self._item_finished(r, rw, res, v))
            done += 1
            self._invoke(lambda d=done: self.progress_bar.setValue(d))
            # Verify-after-write chains a Verify job in right
            # behind a successful burn.
            if job["kind"] == "write" and job.get("verify_after") \
                    and rc == 0 and not self._stop:
                vjob = {"kind": "verify", "image": job["image"],
                        "device": job["device"]}
                holder = {}

                def _chain(h=holder, vj=vjob):
                    it = self._add_job(vj)
                    it["name"] = "Verify (after write): " + \
                        Path(vj["image"]).name
                    self._refresh_row(next(
                        i for i, x in enumerate(self.items)
                        if x is it))
                    h["item"] = it
                    self.progress_bar.setMaximum(
                        len(self.items))
                self._invoke(_chain)
                # _invoke runs synchronously on the GUI thread
                # only via the timer; wait briefly for the chain.
                for _ in range(50):
                    if "item" in holder:
                        break
                    threading.Event().wait(0.1)
                if "item" in holder:
                    work.insert(idx + 1, holder["item"])
            idx += 1
        self._invoke(self._batch_finished)

    def _invoke(self, fn):
        if threading.current_thread() is threading.main_thread():
            fn()
        else:
            self._ui_queue.append(fn)

    def _drain_ui_queue(self):
        while getattr(self, "_ui_queue", None):
            fn = self._ui_queue.pop(0)
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def _verdict(self, results, rc):
        fails = sum(1 for r in results if r["result"] == "FAIL")
        passes = sum(1 for r in results if r["result"] == "PASS")
        if rc != 0 and not results:
            return "Error (exit %d)" % rc
        if fails:
            return "%d failed, %d passed" % (fails, passes)
        if passes:
            return "All checks passed (%d)" % passes
        return "Done (exit %d)" % rc

    def _item_started(self, row):
        item = self.items[row]
        item["status"] = "Running"
        item["raw"] = ""
        item["results"] = []
        self._refresh_row(row)
        self.table.selectRow(row)
        self._set_stage("Job %d: %s" % (row + 1, item["name"]))

    def _item_finished(self, row, raw, results, verdict):
        item = self.items[row]
        item["raw"] = raw
        item["results"] = results
        item["status"] = "Done"
        item["result"] = verdict
        self._refresh_row(row)
        if self._selected_index() == row:
            self._show_item(row)

    def _batch_finished(self):
        self._running = False
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        done = sum(1 for it in self.items
                   if it["status"] == "Done")
        self._set_stage("Batch finished: %d job(s) ran." % done)

    # ----------------------------------------------------------
    # Smoke hook (scripted acceptance driver; inert otherwise)
    # ----------------------------------------------------------
    def _maybe_smoke(self):
        cfg_path = os.environ.get("DISC_TOOLS_SMOKE")
        if not cfg_path:
            return
        try:
            self._smoke_cfg = json.loads(Path(cfg_path).read_text())
        except Exception:  # noqa: BLE001
            return
        self._smoke_state = {"events": []}
        self._smoke_phase = "setup"
        self._smoke_ticks = 0
        self._smoke_timer = QTimer(self)
        self._smoke_timer.timeout.connect(self._smoke_tick)
        self._smoke_timer.start(250)

    def _smoke_shot(self, path):
        if path:
            self.grab().save(path)
            self._smoke_state["events"].append("shot:" + path)

    def _smoke_net_action(self, act):
        st = self._smoke_state
        rec = {"action": act.get("do")}
        st.setdefault("net", []).append(rec)
        try:
            if act.get("tab") is not None:
                self.tabs.setCurrentIndex(act["tab"])
            if act.get("do") == "mount":
                vals = {"server": act.get("server", ""),
                        "share": act.get("share", ""),
                        "user": act.get("user", ""),
                        "password": act.get("password", "")}
                try:
                    rec["root"] = engine.resolve_share(
                        vals["server"], vals["share"],
                        user=vals["user"],
                        password=vals["password"])
                except engine.EngineError as e:
                    rec["error"] = str(e)
                if rec.get("root"):
                    self._remember_share(vals["server"],
                                         vals["share"])
            elif act.get("do") == "dialog":
                dlg = ShareDialog(self, self._net_data())
                dlg.server.setCurrentText(act.get("server", ""))
                dlg.share.setCurrentText(act.get("share", ""))
                rec["values"] = dlg.values()
                dlg.close()
            elif act.get("do") == "browse":
                self._smoke_drive_browse(act, rec)
            elif act.get("do") == "set_text":
                w = getattr(self, act["edit"])
                if hasattr(w, "setText"):
                    w.setText(act.get("text", ""))
                else:  # editable combo (device pickers)
                    w.setCurrentText(act.get("text", ""))
            elif act.get("do") == "set_check":
                getattr(self, act["edit"]).setChecked(
                    bool(act.get("checked", True)))
            elif act.get("do") == "add_form":
                idx = self.tabs.currentIndex()
                (self._add_write, self._add_build,
                 self._add_read, self._add_verify,
                 lambda: None)[idx]()
                rec["queue_len"] = len(self.items)
                if self.items:
                    rec["last_job_image"] = \
                        self.items[-1]["job"].get("image")
                    rec["last_job_archive"] = \
                        self.items[-1]["job"].get(
                            "source_archive")
            elif act.get("do") == "verify_sibling":
                img = self.items[-1]["job"]["image"]
                sib = str(Path(img).with_suffix(
                    act.get("suffix", ".bin")))
                self._add_job({"kind": "verify", "image": sib,
                               "device": act.get("device", "")})
                rec["verify_image"] = sib
        except Exception as e:  # noqa: BLE001
            rec["exception"] = "%s: %s" % (type(e).__name__, e)
        if act.get("shot"):
            self._smoke_shot(act["shot"])

    def _smoke_drive_browse(self, act, rec):
        """Drive the real Network... flow end to end: the slot
        opens the ShareDialog, a timer fills and accepts it,
        the mount runs, and the file dialog that follows is
        pointed at the requested file and accepted. Records
        which dialogs actually appeared and what the target
        field ended up holding."""
        edit = getattr(self, act["edit"])
        seen = []
        state = {"tries": 0, "filled": False}
        timer = QTimer(self)

        def drive():
            state["tries"] += 1
            w = QApplication.activeModalWidget()
            if isinstance(w, ShareDialog) \
                    and not state["filled"]:
                state["filled"] = True
                seen.append("share-dialog")
                w.server.setCurrentText(act.get("server", ""))
                w.share.setCurrentText(act.get("share", ""))
                if act.get("user"):
                    w.user.setText(act["user"])
                if act.get("password"):
                    w.password.setText(act["password"])
                w.accept()
            elif isinstance(w, QFileDialog):
                seen.append("file-dialog")
                if act.get("pick"):
                    w.selectFile(act["pick"])
                timer.stop()
                w.accept()
            if state["tries"] > 300:
                timer.stop()

        timer.timeout.connect(drive)
        timer.start(100)
        if act.get("save"):
            self._browse_share_save(
                edit, "Smoke save", "",
                strip_ext=bool(act.get("strip_ext")))
        elif act.get("dir"):
            self._browse_share_dir(edit)
        else:
            self._browse_share(edit, act.get("kind", "image"))
        timer.stop()
        rec["dialogs_seen"] = seen
        rec["edit_text"] = edit.text()

    def _smoke_tick(self):
        cfg = self._smoke_cfg
        st = self._smoke_state
        phase = self._smoke_phase
        self._smoke_ticks += 1
        if phase == "net":
            # A net action is driving modal dialogs; the timer
            # must not re-enter the setup block underneath it.
            return
        if phase == "setup":
            settled = self._setup_done or self._setup_error \
                or getattr(self, "_setup_stopped", False)
            if (settled and self._scan_done) \
                    or self._smoke_ticks > 240:
                st["stage_after_setup"] = self.stage_label.text()
                st["setup_error"] = self._setup_error
                st["setup_stopped"] = bool(
                    getattr(self, "_setup_stopped", False))
                st["dep_offers"] = list(
                    getattr(self, "_dep_offers", []))
                st["drives"] = list(
                    getattr(self, "_drives", []))
                st["scan_done"] = bool(
                    getattr(self, "_scan_done", False))
                self._smoke_shot(cfg.get("shot_setup"))
                self._smoke_phase = "net"
                for act in cfg.get("net_actions", []):
                    self._smoke_net_action(act)
                for job in cfg.get("add_jobs", []):
                    self._add_job(job)
                st["commands"] = [
                    engine.preview_command(it["job"])
                    for it in self.items]
                st["queue"] = [
                    {"kind": it["job"]["kind"],
                     "name": it["name"]} for it in self.items]
                self._smoke_phase = "run" if cfg.get("run_queue") \
                    else "finish"
            return
        if phase == "run":
            if not self._running and not st.get("run_started"):
                st["run_started"] = True
                self._run_queue()
                self._smoke_phase = "mid"
            return
        if phase == "mid":
            if any(it["status"] == "Running"
                   for it in self.items):
                st["mid_run_stage"] = self.stage_label.text()
                self._smoke_shot(cfg.get("shot_mid"))
                self._smoke_phase = "wait_done"
            elif not self._running:
                self._smoke_phase = "finish"
            return
        if phase == "wait_done":
            if not self._running:
                self._smoke_phase = "finish"
            return
        if phase == "finish":
            self._smoke_timer.stop()
            st["stage_final"] = self.stage_label.text()
            st["items"] = [
                {"name": it["name"], "status": it["status"],
                 "result": it["result"], "results": it["results"],
                 "raw_tail": it["raw"][-4000:]}
                for it in self.items]
            self._smoke_shot(cfg.get("shot_done"))
            out = cfg.get("out_state")
            if out:
                Path(out).write_text(json.dumps(st, indent=1))
            QTimer.singleShot(200, self.close)


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win._maybe_smoke()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
