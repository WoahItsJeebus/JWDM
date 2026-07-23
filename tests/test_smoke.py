"""Application-shell smoke tests."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QAbstractAnimation, QPoint, QPointF, Qt
from PySide6.QtGui import QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
)

from jwdm import __version__
from jwdm.config import AppSettings
from jwdm.logging_config import APPLICATION_LOGGER, configure_logging
from jwdm.pipeline.candidate import CandidateState
from jwdm.pipeline.models import ScanRoot
from jwdm.services.candidate_registry import CandidateRegistry
from jwdm.services.scan import ScanService
from jwdm.ui.main_window import MainWindow
from jwdm.ui.candidate_dialogs import CandidateReviewDialog
from jwdm.ui.manual_dialogs import CategoryCorrectionDialog, ReviewDialog
from jwdm.ui.settings_dialogs import DownloadsRelocationDialog, SettingsDialog
from jwdm.ui.smooth_scroll import SmoothScrollArea
from jwdm.ui.tray import TrayController


@pytest.fixture(scope="module")
def application() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def test_package_has_version() -> None:
    assert __version__ == "1.0.0"


def test_main_window_and_tray_shell(application: QApplication) -> None:
    window = MainWindow()
    tray = TrayController(application, window, lambda: None)

    assert window.windowTitle() == "JWDM"
    organize_button = window.findChild(QPushButton, "organizeButton")
    assert organize_button is not None
    assert organize_button.isEnabled()
    scan_status = window.findChild(QLabel, "manualScanStatus")
    scan_progress = window.findChild(QProgressBar, "manualScanProgress")
    assert scan_status is not None
    assert scan_progress is not None
    assert scan_status.text() == "Manual scan: ready"
    assert scan_progress.format() == "Ready"
    window.set_manual_scan_state(
        "Manual scan: discovering \u2014 3 files found", active=True
    )
    assert scan_progress.minimum() == 0
    assert scan_progress.maximum() == 0
    assert not organize_button.isEnabled()
    assert not window.browse_library_button.isEnabled()
    window.set_manual_scan_state(
        "Manual scan: analyzing 3 of 8",
        active=True,
        completed=3,
        total=8,
    )
    assert scan_progress.maximum() == 8
    assert scan_progress.value() == 3
    window.set_manual_scan_state(
        "Manual scan: complete \u2014 8 files",
        active=False,
        completed=8,
        total=8,
    )
    assert scan_progress.value() == 8
    assert organize_button.isEnabled()
    version_label = window.findChild(QLabel, "versionLabel")
    assert version_label is not None
    assert version_label.text() == f"v{__version__}"
    version_color = version_label.palette().color(QPalette.ColorRole.WindowText)
    assert version_color.alphaF() > 0.7
    assert version_color != window.palette().color(QPalette.ColorRole.Mid)
    assert tray.open_action.text() == "Open JWDM"
    assert tray.organize_action.isEnabled()
    assert not tray.pause_action.isEnabled()
    tray.bind_automatic(lambda: None, lambda: None)
    tray.set_automatic_state(True, False, 2, 1)
    assert tray.pause_action.isEnabled()
    assert not tray.resume_action.isEnabled()
    assert tray.counts_action.text() == "Pending: 2 • Review: 1"
    assert tray.destination_action.text() == "Destination: not configured"
    assert tray.settings_action.text() == "Settings"
    assert tray.exit_action.text() == "Exit"

    tray.icon.hide()
    window.close()


def test_main_window_prioritizes_candidate_list_and_double_click_review(
    application: QApplication, tmp_path: Path
) -> None:
    window = MainWindow()
    window.resize(1000, 1300)
    window.show()
    application.processEvents()

    manual = window.findChild(QGroupBox, "manualOrganizationGroup")
    assert manual is not None
    assert window.rules_button.mapTo(window, window.rules_button.rect().topLeft()).y() < (
        manual.mapTo(window, manual.rect().topLeft()).y()
    )
    assert window.candidate_table.maximumHeight() > 10_000
    assert window.candidate_table.height() > 350
    assert window.incoming_edit.height() < 50
    assert window.automatic_status_label.height() < 50

    incoming = tmp_path / "incoming"
    candidate_path = incoming / "unknown.widget"
    registry = CandidateRegistry()
    candidate = registry.register_event(
        candidate_path, incoming, "created", datetime.now(UTC)
    )
    candidate = registry.transition(
        candidate.candidate_id,
        CandidateState.NEEDS_REVIEW,
        "No built-in rule for .widget",
    )
    assert candidate is not None
    requested_rules: list[object] = []
    requested_reviews: list[object] = []
    window.candidate_rule_requested.connect(requested_rules.append)
    window.candidate_review_requested.connect(requested_reviews.append)
    window.set_candidates((candidate,))
    window._candidate_double_clicked(0, 2)
    window._candidate_double_clicked(0, 0)
    assert requested_rules == [candidate]
    assert requested_reviews == [candidate]

    review = CandidateReviewDialog(candidate)
    assert review.add_rule_button is not None
    assert ".widget" in review.add_rule_button.text()
    review.close()

    moved = registry.transition(candidate.candidate_id, CandidateState.MOVED, "done")
    assert moved is not None
    window.set_candidates((moved,))
    assert window.candidate_table.rowCount() == 0
    assert window.candidate_counts_label.text().startswith("Pending: 0")
    window.close()


def test_main_window_scrolls_instead_of_clipping_candidate_table(
    application: QApplication,
) -> None:
    window = MainWindow()
    window.resize(window.minimumWidth(), window.minimumHeight())
    window.show()
    application.processEvents()

    scroll = window.findChild(QScrollArea, "mainContentScroll")
    scope_note = window.findChild(QLabel, "automaticScopeNote")
    assert scroll is not None
    assert scope_note is not None
    assert scroll.verticalScrollBar().maximum() > 0
    assert window.candidate_table.height() >= window.candidate_table.minimumHeight()

    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    application.processEvents()
    note_top = scope_note.mapTo(scroll.viewport(), scope_note.rect().topLeft()).y()
    assert 0 <= note_top < scroll.viewport().height()
    window.close()


def test_main_window_mouse_wheel_scroll_is_animated(
    application: QApplication,
) -> None:
    window = MainWindow()
    window.resize(window.minimumWidth(), window.minimumHeight())
    window.show()
    application.processEvents()

    scroll = window.findChild(SmoothScrollArea, "mainContentScroll")
    assert scroll is not None
    scroll_bar = scroll.verticalScrollBar()
    scroll_bar.setValue(scroll_bar.minimum())
    wheel = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )

    scroll.wheelEvent(wheel)

    assert wheel.isAccepted()
    assert (
        scroll._wheel_animation.state()
        is QAbstractAnimation.State.Running
    )
    target = int(scroll._wheel_animation.endValue())
    assert target > scroll_bar.minimum()
    QTest.qWait(scroll.WHEEL_ANIMATION_DURATION_MS + 40)
    assert scroll_bar.value() == target
    window.close()


def test_structured_file_logging(tmp_path: Path) -> None:
    log_path = configure_logging(tmp_path)
    logger = logging.getLogger(APPLICATION_LOGGER)
    logger.info(
        "smoke event",
        extra={"event": "smoke_test", "operation_id": "test-operation"},
    )
    for handler in logger.handlers:
        handler.flush()

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["level"] == "INFO"
    assert payload["event"] == "smoke_test"
    assert payload["message"] == "smoke event"
    assert payload["operation_id"] == "test-operation"


def test_review_dialog_preselects_only_ready_items(
    application: QApplication, tmp_path: Path
) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    (source / "ready.pdf").write_text("ready", encoding="utf-8")
    (source / "review.unknown").write_text("review", encoding="utf-8")
    plan = ScanService().build_plan((ScanRoot(source),), library)

    dialog = ReviewDialog(plan)
    dialog.show()
    application.processEvents()

    assert [item.source.name for item in dialog.selected_items()] == ["ready.pdf"]
    header = dialog.table.horizontalHeader()
    assert all(
        header.sectionResizeMode(column) is QHeaderView.ResizeMode.Interactive
        for column in range(dialog.table.columnCount())
    )
    ready_row = next(
        row
        for row in range(dialog.table.rowCount())
        if dialog.table.item(row, 2).text() == "ready.pdf"
    )
    assert dialog.table.item(ready_row, 2).toolTip() == str(source / "ready.pdf")
    assert dialog.table.item(ready_row, 4).text() == str(Path("Documents") / "ready.pdf")
    assert dialog.table.item(ready_row, 4).toolTip() == str(
        library / "Documents" / "ready.pdf"
    )
    column_width = sum(dialog.table.columnWidth(column) for column in range(7))
    assert column_width <= dialog.table.viewport().width()
    assert dialog.table.columnWidth(6) >= 180
    screen = dialog.screen()
    assert dialog.width() <= int(screen.availableGeometry().width() * 0.96) + 1

    review_item = next(item for item in plan.items if item.source.name == "review.unknown")
    correction_dialog = CategoryCorrectionDialog(review_item)
    assert ".unknown" in correction_dialog.create_rule.text()
    assert not correction_dialog.create_rule.isChecked()
    correction_dialog.category.setText("Custom/Reviewed")
    correction_dialog.create_rule.setChecked(True)
    correction_dialog.accept()
    correction = correction_dialog.correction()
    assert correction.category == "Custom/Reviewed"
    assert correction.create_rule

    dialog.close()


def test_downloads_settings_expose_explicit_relocate_and_restore_controls(
    application: QApplication, tmp_path: Path
) -> None:
    settings = SettingsDialog(AppSettings())
    relocate = settings.findChild(QPushButton, "relocateDownloadsButton")
    restore = settings.findChild(QPushButton, "restoreDownloadsButton")
    assert relocate is not None
    assert restore is not None
    assert not relocate.isEnabled()
    assert not restore.isEnabled()
    assert settings.route_unknown.isChecked() is False

    first = tmp_path / "incoming-one"
    second = tmp_path / "incoming-two"
    settings.set_incoming_paths((first, second))
    settings.route_unknown.setChecked(True)
    selected = settings.selected_settings()
    assert selected.configured_incoming_paths == (first, second)
    assert selected.route_unknown_to_folder

    current = tmp_path / "Downloads"
    settings.set_downloads_status(
        current,
        "No JWDM Downloads relocation is recorded.",
        can_relocate=True,
        can_restore=False,
    )
    assert relocate.isEnabled()
    assert not restore.isEnabled()
    assert str(current) in settings.downloads_path.text()

    confirmation = DownloadsRelocationDialog(current)
    assert "Existing files remain" in confirmation.findChild(QLabel).text()
    assert confirmation.use_as_incoming.isChecked()
    confirmation.close()
    settings.close()
