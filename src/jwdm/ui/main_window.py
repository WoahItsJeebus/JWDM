"""JWDM main window for manual and automatic organization workflows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from jwdm import __version__
from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState
from jwdm.ui.icons import build_application_icon


class MainWindow(QMainWindow):
    """Present application state and emit user intents to controllers."""

    organize_requested = Signal()
    library_browse_requested = Signal()
    history_requested = Signal()
    undo_requested = Signal()
    incoming_browse_requested = Signal()
    automatic_toggle_requested = Signal()
    automatic_pause_requested = Signal()
    settings_requested = Signal()
    rules_requested = Signal()
    candidate_review_requested = Signal(object)
    candidate_rule_requested = Signal(object)
    close_requested = Signal(object)
    library_path_changed = Signal(object)
    incoming_path_changed = Signal(object)
    incoming_paths_changed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle("JWDM")
        self.setWindowIcon(build_application_icon())
        self.setMinimumSize(800, 690)
        self._automatic_running = False
        self._manual_scan_running = False
        self._incoming_paths: tuple[Path, ...] = ()
        self._visible_candidates: tuple[CandidateSnapshot, ...] = ()

        title = QLabel("JWDM")
        title.setObjectName("applicationTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = title.font()
        title_font.setPointSize(28)
        title_font.setBold(True)
        title.setFont(title_font)

        subtitle = QLabel("Jeebus' Windows Download Manager")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        library_label = QLabel("Organized library")
        self.library_edit = QLineEdit()
        self.library_edit.setObjectName("libraryPath")
        self.library_edit.setReadOnly(True)
        self.library_edit.setPlaceholderText("Choose where categorized files will be placed")
        self.browse_library_button = QPushButton("Browse…")
        self.browse_library_button.setObjectName("browseLibraryButton")
        self.browse_library_button.clicked.connect(self.library_browse_requested.emit)
        library_row = QHBoxLayout()
        library_row.addWidget(self.library_edit, stretch=1)
        library_row.addWidget(self.browse_library_button)
        self.destination_status_label = QLabel("Destination: not configured")
        self.destination_status_label.setObjectName("destinationStatus")

        self.organize_button = QPushButton("Organize folders…")
        self.organize_button.setObjectName("organizeButton")
        self.organize_button.setMinimumHeight(50)
        self.organize_button.clicked.connect(self.organize_requested.emit)

        self.manual_scan_status_label = QLabel("Manual scan: ready")
        self.manual_scan_status_label.setObjectName("manualScanStatus")
        self.manual_scan_progress = QProgressBar()
        self.manual_scan_progress.setObjectName("manualScanProgress")
        self.manual_scan_progress.setRange(0, 1)
        self.manual_scan_progress.setValue(0)
        self.manual_scan_progress.setFormat("Ready")

        self.history_button = QPushButton("History")
        self.history_button.setObjectName("historyButton")
        self.history_button.clicked.connect(self.history_requested.emit)
        self.undo_button = QPushButton("Undo last move")
        self.undo_button.setObjectName("undoButton")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.undo_requested.emit)
        self.rules_button = QPushButton("Rules")
        self.rules_button.setObjectName("rulesButton")
        self.rules_button.clicked.connect(self.rules_requested.emit)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        primary_actions = QHBoxLayout()
        primary_actions.addWidget(self.rules_button)
        primary_actions.addWidget(self.settings_button)
        primary_actions.addStretch()
        manual_actions = QHBoxLayout()
        manual_actions.addWidget(self.history_button)
        manual_actions.addWidget(self.undo_button)
        manual_actions.addStretch()

        manual_group = QGroupBox("Manual organization")
        manual_group.setObjectName("manualOrganizationGroup")
        manual_layout = QVBoxLayout(manual_group)
        manual_layout.addWidget(self.organize_button)
        manual_layout.addWidget(self.manual_scan_status_label)
        manual_layout.addWidget(self.manual_scan_progress)
        manual_layout.addLayout(manual_actions)

        self.incoming_edit = QLineEdit()
        self.incoming_edit.setObjectName("incomingPath")
        self.incoming_edit.setReadOnly(True)
        self.incoming_edit.setPlaceholderText("Configure one or more incoming folders")
        self.browse_incoming_button = QPushButton("Add…")
        self.browse_incoming_button.setObjectName("browseIncomingButton")
        self.browse_incoming_button.clicked.connect(self.incoming_browse_requested.emit)
        incoming_row = QHBoxLayout()
        incoming_row.addWidget(self.incoming_edit, stretch=1)
        incoming_row.addWidget(self.browse_incoming_button)

        self.automatic_toggle_button = QPushButton("Start automatic organization")
        self.automatic_toggle_button.setObjectName("automaticToggleButton")
        self.automatic_toggle_button.clicked.connect(self.automatic_toggle_requested.emit)
        self.automatic_pause_button = QPushButton("Pause")
        self.automatic_pause_button.setObjectName("automaticPauseButton")
        self.automatic_pause_button.setEnabled(False)
        self.automatic_pause_button.clicked.connect(self.automatic_pause_requested.emit)
        automatic_actions = QHBoxLayout()
        automatic_actions.addWidget(self.automatic_toggle_button)
        automatic_actions.addWidget(self.automatic_pause_button)
        automatic_actions.addStretch()

        self.automatic_status_label = QLabel("Stopped — settings and pending candidates are saved.")
        self.automatic_status_label.setObjectName("automaticStatus")
        self.candidate_counts_label = QLabel("Pending: 0 • Needs review: 0")
        self.candidate_counts_label.setObjectName("candidateCounts")
        automatic_status_row = QHBoxLayout()
        automatic_status_row.addWidget(self.automatic_status_label, stretch=1)
        automatic_status_row.addWidget(self.candidate_counts_label)

        self.candidate_table = QTableWidget(0, 3)
        self.candidate_table.setObjectName("candidateTable")
        self.candidate_table.setHorizontalHeaderLabels(["File", "State", "Detail"])
        self.candidate_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.candidate_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.candidate_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.candidate_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.candidate_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.candidate_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.candidate_table.setMinimumHeight(180)
        self.candidate_table.cellDoubleClicked.connect(self._candidate_double_clicked)

        automatic_group = QGroupBox("Automatic organization")
        automatic_group.setObjectName("automaticOrganizationGroup")
        automatic_layout = QVBoxLayout(automatic_group)
        automatic_layout.addWidget(QLabel("Incoming folders (top level only)"))
        automatic_layout.addLayout(incoming_row)
        automatic_layout.addLayout(automatic_actions)
        automatic_layout.addLayout(automatic_status_row)
        automatic_layout.addWidget(self.candidate_table, stretch=1)

        self.activity_label = QLabel("No organization activity yet.")
        self.activity_label.setObjectName("recentActivity")
        self.activity_label.setWordWrap(True)
        self.activity_label.setStyleSheet(
            "QLabel { background: palette(alternate-base); border-radius: 6px; padding: 10px; }"
        )

        scope_note = QLabel(
            "Automatic mode only moves recognized files allowed by the confidence policy "
            "after stability and exclusive-access checks. Unrecognized items follow the "
            "Unknown-folder policy in Settings."
        )
        scope_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scope_note.setWordWrap(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(38, 24, 38, 30)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(primary_actions)
        layout.addWidget(library_label)
        layout.addLayout(library_row)
        layout.addWidget(self.destination_status_label)
        layout.addWidget(manual_group)
        layout.addWidget(automatic_group, stretch=1)
        layout.addWidget(QLabel("Recent activity"))
        layout.addWidget(self.activity_label)
        layout.addWidget(scope_note)

        content = QWidget()
        content.setLayout(layout)
        self.setCentralWidget(content)

        self.version_label = QLabel(f"v{__version__}")
        self.version_label.setObjectName("versionLabel")
        self.version_label.setToolTip(f"JWDM version {__version__}")
        version_palette = self.version_label.palette()
        version_color = version_palette.color(QPalette.ColorRole.WindowText)
        version_color.setAlphaF(0.72)
        version_palette.setColor(QPalette.ColorRole.WindowText, version_color)
        self.version_label.setPalette(version_palette)
        self.version_label.setStyleSheet(
            "QLabel { padding: 0 6px 2px 0; }"
        )
        self.statusBar().addPermanentWidget(self.version_label)

    @property
    def library_path(self) -> Path | None:
        value = self.library_edit.text().strip()
        return Path(value) if value else None

    @property
    def incoming_path(self) -> Path | None:
        return self._incoming_paths[0] if self._incoming_paths else None

    @property
    def incoming_paths(self) -> tuple[Path, ...]:
        return self._incoming_paths

    @property
    def file_operations_busy(self) -> bool:
        return self._automatic_running or self._manual_scan_running

    def set_library_path(self, path: Path) -> None:
        self.library_edit.setText(str(path))
        self.library_path_changed.emit(path)

    def set_incoming_path(self, path: Path) -> None:
        self.set_incoming_paths((path,))
        self.incoming_path_changed.emit(path)

    def set_incoming_paths(self, paths: tuple[Path, ...]) -> None:
        self._incoming_paths = paths
        if not paths:
            self.incoming_edit.clear()
            self.incoming_edit.setToolTip("")
        elif len(paths) == 1:
            self.incoming_edit.setText(str(paths[0]))
            self.incoming_edit.setToolTip(str(paths[0]))
        else:
            self.incoming_edit.setText(f"{len(paths)} incoming folders configured")
            self.incoming_edit.setToolTip("\n".join(str(path) for path in paths))
        self.incoming_paths_changed.emit(paths)

    def set_recent_activity(self, message: str) -> None:
        self.activity_label.setText(message)

    def set_destination_status(self, available: bool, detail: str) -> None:
        prefix = "Destination available" if available else "Destination unavailable"
        self.destination_status_label.setText(f"{prefix} — {detail}")

    def set_undo_available(self, available: bool) -> None:
        self.undo_button.setEnabled(available)

    def set_automatic_state(self, running: bool, paused: bool) -> None:
        self._automatic_running = running
        self.automatic_toggle_button.setText(
            "Stop automatic organization" if running else "Start automatic organization"
        )
        self.automatic_pause_button.setEnabled(running)
        self.automatic_pause_button.setText("Resume" if paused else "Pause")
        self._refresh_path_controls()
        if not running:
            status = "Stopped — settings and pending candidates are saved."
        elif paused:
            status = "Paused — new events are queued but no candidates are processed."
        else:
            status = "Running — waiting for safe, stable incoming files."
        self.automatic_status_label.setText(status)

    def set_manual_scan_state(
        self,
        status: str,
        *,
        active: bool,
        completed: int = 0,
        total: int | None = None,
    ) -> None:
        """Present manual-scan activity without exposing controller details."""

        self._manual_scan_running = active
        self.manual_scan_status_label.setText(status)
        self.organize_button.setEnabled(not active)
        self._refresh_path_controls()
        if active and total is None:
            self.manual_scan_progress.setRange(0, 0)
            self.manual_scan_progress.setFormat("Discovering files\u2026")
            return
        if total is not None:
            maximum = max(total, 1)
            self.manual_scan_progress.setRange(0, maximum)
            self.manual_scan_progress.setValue(min(completed, maximum))
            self.manual_scan_progress.setFormat(
                "%v of %m files" if active else "Complete \u2014 %v files"
            )
            return
        self.manual_scan_progress.setRange(0, 1)
        self.manual_scan_progress.setValue(0)
        self.manual_scan_progress.setFormat("Stopped")

    def _refresh_path_controls(self) -> None:
        self.browse_incoming_button.setEnabled(not self._automatic_running)
        self.browse_library_button.setEnabled(
            not self._automatic_running and not self._manual_scan_running
        )

    def set_candidates(self, candidates: tuple[CandidateSnapshot, ...]) -> None:
        active = tuple(
            candidate
            for candidate in candidates
            if candidate.state not in {CandidateState.MOVED, CandidateState.EXCLUDED}
        )
        visible = tuple(reversed(active[-100:]))
        self._visible_candidates = visible
        self.candidate_table.setRowCount(len(visible))
        for row, candidate in enumerate(visible):
            values = (candidate.source_path.name, candidate.state.value, candidate.detail)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(str(candidate.source_path))
                self.candidate_table.setItem(row, column, item)
        pending = sum(
            candidate.state
            not in {CandidateState.MOVED, CandidateState.FAILED, CandidateState.EXCLUDED}
            for candidate in candidates
        )
        review = sum(
            candidate.state is CandidateState.NEEDS_REVIEW for candidate in candidates
        )
        self.candidate_counts_label.setText(f"Pending: {pending} • Needs review: {review}")

    def _candidate_double_clicked(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._visible_candidates):
            return
        candidate = self._visible_candidates[row]
        if column == 2 and candidate.detail.startswith("No built-in rule for "):
            self.candidate_rule_requested.emit(candidate)
            return
        self.candidate_review_requested.emit(candidate)

    def bring_to_front(self) -> None:
        """Show and focus the existing main window."""

        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Delegate the configured close decision to the application controller."""

        event.accept()
        self.close_requested.emit(event)
        if event.isAccepted():
            super().closeEvent(event)
