"""Coordinate durable settings, destination status, startup, and close behavior."""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from jwdm.config import AppSettings, ExtensionRule, RuleAction
from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.state import StateError, StateRepository
from jwdm.services.library_destination import LibraryDestinationService
from jwdm.services.rule_suggestions import suggested_extension
from jwdm.services.downloads import (
    DownloadsRelocationError,
    DownloadsRelocationService,
    DownloadsStatus,
)
from jwdm.services.startup import StartupError, StartupManager
from jwdm.ui.main_window import MainWindow
from jwdm.ui.settings_dialogs import (
    DownloadsRelocationDialog,
    ExtensionRuleDialog,
    RulesDialog,
    SettingsDialog,
)
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
        library_destination: LibraryDestinationService | None = None,
        downloads: DownloadsRelocationService | None = None,
    ) -> None:
        self._application = application
        self._window = window
        self._repository = repository
        self._startup = startup
        self._settings = settings
        self._library_destination = library_destination
        self._downloads = downloads
        self._destination_status = None
        self._tray: TrayController | None = None
        self._tray_available = False
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.settings")

        if settings.library_path is not None:
            window.set_library_path(settings.library_path)
        if settings.configured_incoming_paths:
            window.set_incoming_paths(settings.configured_incoming_paths)

        window.settings_requested.connect(self.show_settings)
        window.rules_requested.connect(self.show_rules)
        window.close_requested.connect(self.handle_close)
        window.library_path_changed.connect(self._path_changed)
        window.incoming_paths_changed.connect(self._paths_changed)
        self._destination_timer = QTimer(window)
        self._destination_timer.setInterval(2000)
        self._destination_timer.timeout.connect(self.refresh_destination_status)
        if library_destination is not None and settings.library_path is not None:
            self.refresh_destination_status()
            self._destination_timer.start()

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
        if self._destination_status is not None:
            tray.set_destination_status(
                self._destination_status.available,
                self._destination_status.detail,
            )

    def show_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self._window)
        self._refresh_downloads_dialog(dialog)
        dialog.relocate_downloads_button.clicked.connect(
            lambda: self._relocate_downloads(dialog)
        )
        dialog.restore_downloads_button.clicked.connect(
            lambda: self._restore_downloads(dialog)
        )
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
        self._window.set_incoming_paths(updated.configured_incoming_paths)
        self._logger.info("Settings saved", extra={"event": "settings_saved"})

    def _relocate_downloads(self, dialog: SettingsDialog) -> None:
        if self._downloads is None:
            return
        if self._window.file_operations_busy:
            QMessageBox.information(
                self._window,
                "Stop organization first",
                "Stop automatic organization and wait for the manual scan to finish "
                "before changing the Windows Downloads location.",
            )
            return
        try:
            status = self._downloads.status()
        except (DownloadsRelocationError, StateError) as error:
            QMessageBox.critical(
                self._window, "Downloads location unavailable", str(error)
            )
            self._refresh_downloads_dialog(dialog)
            return
        if status.current_path is None or not status.can_relocate:
            QMessageBox.warning(
                self._window,
                "Downloads cannot be relocated",
                status.detail,
            )
            return
        editor = DownloadsRelocationDialog(status.current_path, dialog)
        if editor.exec() != QDialog.DialogCode.Accepted:
            return
        target = editor.target_path
        if target is None:
            return
        try:
            updated_status = self._downloads.relocate(
                target,
                library_path=self._settings.library_path,
            )
        except (DownloadsRelocationError, StateError) as error:
            QMessageBox.critical(
                self._window,
                "Downloads relocation needs attention",
                "JWDM could not confirm a clean relocation. Existing files were not "
                f"moved. Reopen Settings and check the reported current path.\n\n{error}",
            )
            self._refresh_downloads_dialog(dialog)
            return

        incoming_updated = True
        if editor.use_as_incoming.isChecked() and updated_status.current_path is not None:
            incoming_paths = self._append_incoming(
                self._settings.configured_incoming_paths,
                updated_status.current_path,
            )
            incoming_updated = self._save_incoming_paths(incoming_paths, dialog)
        self._apply_downloads_status(dialog, updated_status)
        message = (
            f"Windows Downloads now points to:\n{updated_status.current_path}\n\n"
            "Existing files were left unchanged."
        )
        if not incoming_updated:
            message += (
                "\n\nThe JWDM incoming-folder setting could not be saved; choose it "
                "again before starting automatic organization."
            )
            QMessageBox.warning(self._window, "Downloads relocated", message)
        else:
            QMessageBox.information(self._window, "Downloads relocated", message)

    def _restore_downloads(self, dialog: SettingsDialog) -> None:
        if self._downloads is None:
            return
        if self._window.file_operations_busy:
            QMessageBox.information(
                self._window,
                "Stop organization first",
                "Stop automatic organization and wait for the manual scan to finish "
                "before restoring the Windows Downloads location.",
            )
            return
        try:
            status = self._downloads.status()
        except (DownloadsRelocationError, StateError) as error:
            QMessageBox.critical(
                self._window, "Downloads location unavailable", str(error)
            )
            self._refresh_downloads_dialog(dialog)
            return
        record = status.record
        if not status.can_restore or record is None:
            QMessageBox.warning(
                self._window,
                "Downloads cannot be restored automatically",
                status.detail,
            )
            return
        confirmation = QMessageBox.question(
            self._window,
            "Restore Windows Downloads?",
            f"Change Windows Downloads from:\n{status.current_path}\n\n"
            f"back to the recorded location:\n{record.original_path}\n\n"
            "Files in both folders will remain unchanged.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            restored = self._downloads.restore()
        except (DownloadsRelocationError, StateError) as error:
            QMessageBox.critical(
                self._window,
                "Downloads restore needs attention",
                "JWDM could not confirm a clean restore. Existing files were not moved. "
                f"Reopen Settings and check the reported current path.\n\n{error}",
            )
            self._refresh_downloads_dialog(dialog)
            return

        incoming_updated = True
        if (
            self._settings.configured_incoming_paths
            and restored.current_path is not None
            and any(
                self._paths_match(path, record.relocated_path)
                for path in self._settings.configured_incoming_paths
            )
        ):
            replaced = tuple(
                restored.current_path
                if self._paths_match(path, record.relocated_path)
                else path
                for path in self._settings.configured_incoming_paths
            )
            incoming_updated = self._save_incoming_paths(
                self._deduplicate_paths(replaced), dialog
            )
        self._apply_downloads_status(dialog, restored)
        message = (
            f"Windows Downloads was restored to:\n{restored.current_path}\n\n"
            "Files in both locations were left unchanged."
        )
        if not incoming_updated:
            message += (
                "\n\nThe JWDM incoming-folder setting could not be updated; verify it "
                "before starting automatic organization."
            )
            QMessageBox.warning(self._window, "Downloads restored", message)
        else:
            QMessageBox.information(self._window, "Downloads restored", message)

    def _refresh_downloads_dialog(self, dialog: SettingsDialog) -> None:
        if self._downloads is None:
            dialog.set_downloads_status(
                None,
                "Windows Downloads relocation is unavailable in this build.",
                can_relocate=False,
                can_restore=False,
            )
            return
        try:
            status = self._downloads.status()
        except (DownloadsRelocationError, StateError) as error:
            self._logger.error(
                "Downloads relocation status unavailable",
                extra={"event": "downloads_status_error"},
                exc_info=True,
            )
            dialog.set_downloads_status(
                None,
                str(error),
                can_relocate=False,
                can_restore=False,
            )
            return
        self._apply_downloads_status(dialog, status)

    @staticmethod
    def _apply_downloads_status(
        dialog: SettingsDialog,
        status: DownloadsStatus,
    ) -> None:
        dialog.set_downloads_status(
            status.current_path,
            status.detail,
            can_relocate=status.can_relocate,
            can_restore=status.can_restore,
        )

    def _save_incoming_paths(
        self,
        paths: tuple[Path, ...],
        dialog: SettingsDialog,
    ) -> bool:
        updated = replace(
            self._settings,
            incoming_path=paths[0] if paths else None,
            incoming_paths=paths,
        )
        try:
            self._repository.save_settings(updated)
        except StateError:
            self._logger.exception(
                "Downloads changed but incoming setting was not saved",
                extra={"event": "downloads_incoming_persistence_error"},
            )
            return False
        self._settings = updated
        self._window.set_incoming_paths(paths)
        dialog.set_base_settings(updated)
        dialog.set_incoming_paths(paths)
        return True

    @staticmethod
    def _paths_match(first: Path, second: Path) -> bool:
        return os.path.normcase(str(first.resolve(strict=False))) == os.path.normcase(
            str(second.resolve(strict=False))
        )

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

    def add_rule_for_path(self, path: Path) -> bool:
        """Open the Rules > Add editor prefilled from one reviewed candidate."""

        extension = suggested_extension(path)
        if extension is None:
            QMessageBox.information(
                self._window,
                "Rule unavailable",
                "This item does not have an extension that can be used by a basic rule.",
            )
            return False
        try:
            existing = self._repository.rules()
        except StateError as error:
            QMessageBox.critical(self._window, "Rules unavailable", str(error))
            return False
        if any(rule.extension.casefold() == extension.casefold() for rule in existing):
            QMessageBox.information(
                self._window,
                "Rule already exists",
                f"A user rule for {extension} already exists. Open Rules to edit it.",
            )
            return False
        editor = ExtensionRuleDialog(
            ExtensionRule(extension, RuleAction.ROUTE, None),
            self._window,
        )
        editor.setWindowTitle("Rules > Add")
        if editor.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            self._repository.upsert_rules((editor.rule(),))
        except StateError as error:
            QMessageBox.critical(self._window, "Rule was not saved", str(error))
            return False
        self._logger.info(
            "Candidate quick rule saved",
            extra={"event": "candidate_rule_saved", "extension": extension},
        )
        return True

    def refresh_destination_status(self) -> None:
        if self._library_destination is None or self._settings.library_path is None:
            return
        try:
            status = self._library_destination.status(self._settings.library_path)
            if status.available and status.path != self._settings.library_path:
                updated = replace(self._settings, library_path=status.path)
                self._repository.save_settings(updated)
                self._settings = updated
                self._window.set_library_path(status.path)
        except (OSError, StateError) as error:
            self._logger.error(
                "Destination status refresh failed",
                extra={"event": "destination_status_error"},
                exc_info=True,
            )
            self._window.set_destination_status(False, str(error))
            if self._tray is not None:
                self._tray.set_destination_status(False, str(error))
            return
        self._destination_status = status
        self._window.set_destination_status(status.available, status.detail)
        if self._tray is not None:
            self._tray.set_destination_status(status.available, status.detail)

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
        self._save_window_paths()

    def _paths_changed(self, _paths: object) -> None:
        self._save_window_paths()

    def _save_window_paths(self) -> None:
        incoming_paths = self._window.incoming_paths
        updated = replace(
            self._settings,
            library_path=self._window.library_path,
            incoming_path=incoming_paths[0] if incoming_paths else None,
            incoming_paths=incoming_paths,
        )
        if updated == self._settings:
            return
        if (
            self._library_destination is not None
            and updated.library_path is not None
            and updated.library_path != self._settings.library_path
        ):
            try:
                self._library_destination.configure(updated.library_path)
                self._destination_status = self._library_destination.status(
                    updated.library_path
                )
            except (OSError, StateError) as error:
                QMessageBox.warning(
                    self._window,
                    "Library identity unavailable",
                    f"JWDM could not bind this folder to its volume.\n\n{error}",
                )
                previous = self._settings.library_path
                self._window.library_edit.setText(str(previous) if previous else "")
                return
            self._window.set_destination_status(
                self._destination_status.available,
                self._destination_status.detail,
            )
            if self._tray is not None:
                self._tray.set_destination_status(
                    self._destination_status.available,
                    self._destination_status.detail,
                )
            if not self._destination_timer.isActive():
                self._destination_timer.start()
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

    @classmethod
    def _append_incoming(
        cls, paths: tuple[Path, ...], candidate: Path
    ) -> tuple[Path, ...]:
        return cls._deduplicate_paths((*paths, candidate))

    @classmethod
    def _deduplicate_paths(cls, paths: tuple[Path, ...]) -> tuple[Path, ...]:
        unique: list[Path] = []
        for path in paths:
            if not any(cls._paths_match(path, existing) for existing in unique):
                unique.append(path)
        return tuple(unique)
