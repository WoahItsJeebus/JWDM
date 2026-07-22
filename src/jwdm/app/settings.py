"""Coordinate durable Phase 3 settings, rules, startup, and close behavior."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from jwdm.config import AppSettings
from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.state import StateError, StateRepository
from jwdm.services.startup import StartupError, StartupManager
from jwdm.ui.main_window import MainWindow
from jwdm.ui.settings_dialogs import RulesDialog, SettingsDialog
from jwdm.ui.tray import TrayController


class SettingsController:
    """Keep persistence and operating-system changes outside Qt widgets."""

    def __init__(
        self,
        application: QApplication,
        window: MainWindow,
        repository: StateRepository,
        startup: StartupManager,
        settings: AppSettings,
    ) -> None:
        self._application = application
        self._window = window
        self._repository = repository
        self._startup = startup
        self._settings = settings
        self._tray: TrayController | None = None
        self._tray_available = False
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.settings")

        if settings.library_path is not None:
            window.set_library_path(settings.library_path)
        if settings.incoming_path is not None:
            window.set_incoming_path(settings.incoming_path)

        window.settings_requested.connect(self.show_settings)
        window.rules_requested.connect(self.show_rules)
        window.close_requested.connect(self.handle_close)
        window.library_path_changed.connect(self._path_changed)
        window.incoming_path_changed.connect(self._path_changed)

    def current(self) -> AppSettings:
        return self._settings

    def synchronize_startup(self) -> None:
        self._startup.synchronize(
            self._settings.start_with_windows,
            self._settings.launch_minimized,
        )

    def set_tray(self, tray: TrayController, available: bool) -> None:
        self._tray = tray
        self._tray_available = available
        tray.bind_settings(self.show_settings)

    def show_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.selected_settings()
        try:
            self._startup.synchronize(
                updated.start_with_windows,
                updated.launch_minimized,
            )
            self._repository.save_settings(updated)
        except (StartupError, StateError) as error:
            try:
                self._startup.synchronize(
                    self._settings.start_with_windows,
                    self._settings.launch_minimized,
                )
            except StartupError:
                self._logger.exception(
                    "Startup entry rollback failed",
                    extra={"event": "startup_rollback_failed"},
                )
            QMessageBox.critical(self._window, "Settings were not saved", str(error))
            return
        self._settings = updated
        self._logger.info("Settings saved", extra={"event": "settings_saved"})

    def show_rules(self) -> None:
        try:
            existing = self._repository.rules()
        except StateError as error:
            QMessageBox.critical(self._window, "Rules unavailable", str(error))
            return
        dialog = RulesDialog(existing, self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._repository.replace_rules(dialog.selected_rules())
        except StateError as error:
            QMessageBox.critical(self._window, "Rules were not saved", str(error))
            return
        self._logger.info(
            "User extension rules saved",
            extra={"event": "rules_saved", "count": len(dialog.selected_rules())},
        )

    def handle_close(self, event: object) -> None:
        if not isinstance(event, QCloseEvent):
            return
        if (
            self._settings.minimize_to_tray
            and self._tray_available
            and self._tray is not None
        ):
            event.ignore()
            self._window.hide()
            if not self._settings.close_notice_shown:
                self._tray.show_close_notice()
                updated = replace(self._settings, close_notice_shown=True)
                try:
                    self._repository.save_settings(updated)
                except StateError:
                    self._logger.exception(
                        "Close-to-tray notice state was not persisted",
                        extra={"event": "close_notice_persistence_error"},
                    )
                else:
                    self._settings = updated
            return
        event.accept()
        QTimer.singleShot(0, self._application.quit)

    def _path_changed(self, _path: Path) -> None:
        updated = replace(
            self._settings,
            library_path=self._window.library_path,
            incoming_path=self._window.incoming_path,
        )
        if updated == self._settings:
            return
        try:
            self._repository.save_settings(updated)
        except StateError as error:
            self._logger.error(
                "Configured paths were not persisted",
                extra={"event": "path_persistence_error"},
                exc_info=True,
            )
            QMessageBox.warning(
                self._window,
                "Path is session-only",
                f"The path is active for this session but could not be saved.\n\n{error}",
            )
            return
        self._settings = updated
