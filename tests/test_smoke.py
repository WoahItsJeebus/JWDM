"""Application-shell smoke tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QHeaderView, QLabel, QPushButton

from jwdm import __version__
from jwdm.logging_config import APPLICATION_LOGGER, configure_logging
from jwdm.pipeline.models import ScanRoot
from jwdm.services.scan import ScanService
from jwdm.ui.main_window import MainWindow
from jwdm.ui.manual_dialogs import ReviewDialog
from jwdm.ui.tray import TrayController


@pytest.fixture(scope="module")
def application() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def test_package_has_version() -> None:
    assert __version__ == "0.5.0"


def test_main_window_and_tray_shell(application: QApplication) -> None:
    window = MainWindow()
    tray = TrayController(application, window, lambda: None)

    assert window.windowTitle() == "JWDM"
    organize_button = window.findChild(QPushButton, "organizeButton")
    assert organize_button is not None
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
    assert sum(dialog.table.columnWidth(column) for column in range(7)) > dialog.width()
    dialog.close()
