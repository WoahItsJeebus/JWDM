from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from jwdm.app.settings import SettingsController
from jwdm.config import (
    AppSettings,
    DownloadsRelocationRecord,
    DownloadsRelocationState,
)
from jwdm.persistence.state import StateRepository
from jwdm.services.downloads import DownloadsStatus
from jwdm.ui.main_window import MainWindow
from jwdm.ui.settings_dialogs import SettingsDialog


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


class _Downloads:
    def __init__(self, original: Path) -> None:
        self.current = original
        self.record: DownloadsRelocationRecord | None = None

    def status(self) -> DownloadsStatus:
        active = bool(
            self.record is not None
            and self.record.state is DownloadsRelocationState.ACTIVE
        )
        return DownloadsStatus(
            True,
            self.current,
            self.record,
            not active,
            active,
            "test status",
        )

    def relocate(
        self, target: Path, *, library_path: Path | None = None
    ) -> DownloadsStatus:
        timestamp = datetime.now(UTC)
        self.record = DownloadsRelocationRecord(
            self.current,
            target,
            DownloadsRelocationState.ACTIVE,
            timestamp,
            timestamp,
        )
        self.current = target
        return self.status()

    def restore(self) -> DownloadsStatus:
        assert self.record is not None
        self.current = self.record.original_path
        self.record = replace(
            self.record,
            state=DownloadsRelocationState.RESTORED,
            updated_at=datetime.now(UTC),
        )
        return self.status()


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


def test_downloads_controller_updates_and_restores_explicit_incoming_choice(
    application: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = tmp_path / "Downloads"
    target = tmp_path / "Incoming"
    original.mkdir()
    target.mkdir()
    repository = StateRepository(tmp_path / "state.db")
    settings = AppSettings()
    repository.save_settings(settings)
    window = MainWindow()
    downloads = _Downloads(original)
    controller = SettingsController(
        application,
        window,
        repository,
        _Startup(),
        settings,
        downloads=downloads,  # type: ignore[arg-type]
    )
    dialog = SettingsDialog(settings)

    class _Choice:
        def __init__(self, *args: object) -> None:
            self.target_path = target
            self.use_as_incoming = self

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def isChecked(self) -> bool:
            return True

    monkeypatch.setattr("jwdm.app.settings.DownloadsRelocationDialog", _Choice)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args: QMessageBox.StandardButton.Yes,
    )

    controller._relocate_downloads(dialog)

    assert controller.current().incoming_path == target
    assert repository.settings().incoming_path == target
    assert window.incoming_path == target

    controller._restore_downloads(dialog)

    assert controller.current().incoming_path == original
    assert repository.settings().incoming_path == original
    assert window.incoming_path == original
    dialog.close()
    window.deleteLater()
