"""Phase 0 smoke tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from jwdm import __version__
from jwdm.logging_config import APPLICATION_LOGGER, configure_logging
from jwdm.ui.main_window import MainWindow
from jwdm.ui.tray import TrayController


@pytest.fixture(scope="module")
def application() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def test_package_has_version() -> None:
    assert __version__ == "0.1.0"


def test_main_window_and_tray_shell(application: QApplication) -> None:
    window = MainWindow()
    tray = TrayController(application, window)

    assert window.windowTitle() == "JWDM"
    organize_button = window.findChild(QPushButton, "organizeButton")
    assert organize_button is not None
    assert not organize_button.isEnabled()
    assert tray.open_action.text() == "Open JWDM"
    assert not tray.organize_action.isEnabled()
    assert tray.exit_action.text() == "Exit"

    tray.icon.hide()
    window.close()


def test_structured_file_logging(tmp_path: Path) -> None:
    log_path = configure_logging(tmp_path)
    logger = logging.getLogger(APPLICATION_LOGGER)
    logger.info("smoke event", extra={"event": "smoke_test"})
    for handler in logger.handlers:
        handler.flush()

    payload = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert payload["level"] == "INFO"
    assert payload["event"] == "smoke_test"
    assert payload["message"] == "smoke event"

