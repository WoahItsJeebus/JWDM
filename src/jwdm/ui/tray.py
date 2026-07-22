"""System-tray controls for manual and automatic workflows."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.ui.icons import build_application_icon
from jwdm.ui.main_window import MainWindow


class TrayController:
    """Own the tray icon and expose the manual workflow from its menu."""

    def __init__(
        self,
        application: QApplication,
        main_window: MainWindow,
        organize_handler: Callable[[], None] | None = None,
    ) -> None:
        self._application = application
        self._main_window = main_window
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.tray")

        self.menu = QMenu()
        self.open_action = self.menu.addAction("Open JWDM")
        self.open_action.triggered.connect(self._main_window.bring_to_front)

        self.organize_action = self.menu.addAction("Organize folders")
        if organize_handler is None:
            self.organize_action.setEnabled(False)
        else:
            self.organize_action.triggered.connect(organize_handler)

        self.status_action = self.menu.addAction(
            "Automatic organization: stopped"
        )
        self.status_action.setEnabled(False)
        self.pause_action = self.menu.addAction("Pause automatic organization")
        self.pause_action.setEnabled(False)
        self.resume_action = self.menu.addAction("Resume automatic organization")
        self.resume_action.setEnabled(False)
        self.counts_action = self.menu.addAction("Pending: 0 • Review: 0")
        self.counts_action.setEnabled(False)
        self.settings_action = self.menu.addAction("Settings")
        self.settings_action.setEnabled(False)
        self.menu.addSeparator()

        self.exit_action = self.menu.addAction("Exit")
        self.exit_action.triggered.connect(self._application.quit)

        self.icon = QSystemTrayIcon(build_application_icon(), self._application)
        self.icon.setToolTip("JWDM")
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._handle_activation)

    def bind_automatic(
        self,
        pause_handler: Callable[[], None],
        resume_handler: Callable[[], None],
    ) -> None:
        self.pause_action.triggered.connect(pause_handler)
        self.resume_action.triggered.connect(resume_handler)

    def bind_settings(self, settings_handler: Callable[[], None]) -> None:
        self.settings_action.setEnabled(True)
        self.settings_action.triggered.connect(settings_handler)

    def show_close_notice(self) -> None:
        self.icon.showMessage(
            "JWDM is still running",
            "Automatic organization remains available from the system tray. Use Exit to stop JWDM.",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def set_automatic_state(
        self, running: bool, paused: bool, pending: int, review: int
    ) -> None:
        state = "paused" if paused else "running" if running else "stopped"
        self.status_action.setText(f"Automatic organization: {state}")
        self.pause_action.setEnabled(running and not paused)
        self.resume_action.setEnabled(running and paused)
        self.counts_action.setText(f"Pending: {pending} • Review: {review}")

    def show(self) -> bool:
        """Show the tray icon when the current desktop supports one."""

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._logger.warning(
                "System tray is unavailable",
                extra={"event": "tray_unavailable"},
            )
            return False

        self.icon.show()
        self._logger.info("System tray icon shown", extra={"event": "tray_started"})
        return True

    def _handle_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._main_window.bring_to_front()
