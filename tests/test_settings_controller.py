from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from jwdm.app.settings import SettingsController
from jwdm.config import AppSettings
from jwdm.persistence.state import StateRepository
from jwdm.ui.main_window import MainWindow


class _Startup:
    def synchronize(self, enabled: bool, launch_minimized: bool) -> None:
        pass


class _Tray:
    def __init__(self) -> None:
        self.notices = 0
        self.settings_handler = None

    def bind_settings(self, handler) -> None:
        self.settings_handler = handler

    def show_close_notice(self) -> None:
        self.notices += 1


@pytest.fixture(scope="module")
def application() -> QApplication:
    existing = QApplication.instance()
    return existing if existing is not None else QApplication([])


def test_close_to_tray_is_persisted_and_notice_is_shown_once(
    application: QApplication, tmp_path: Path
) -> None:
    repository = StateRepository(tmp_path / "state.db")
    settings = AppSettings(minimize_to_tray=True)
    repository.save_settings(settings)
    window = MainWindow()
    controller = SettingsController(
        application,
        window,
        repository,
        _Startup(),
        settings,
    )
    tray = _Tray()
    controller.set_tray(tray, True)

    first = QCloseEvent()
    controller.handle_close(first)
    second = QCloseEvent()
    controller.handle_close(second)

    assert not first.isAccepted()
    assert not second.isAccepted()
    assert tray.notices == 1
    assert repository.settings().close_notice_shown
    window.deleteLater()
