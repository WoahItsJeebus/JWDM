"""System-tray shell for Phase 0."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.ui.icons import build_application_icon
from jwdm.ui.main_window import MainWindow


class TrayController:
    """Own the tray icon and its deliberately limited Phase 0 menu."""

    def __init__(self, application: QApplication, main_window: MainWindow) -> None:
        self._application = application
        self._main_window = main_window
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.tray")

        self.menu = QMenu()
        self.open_action = self.menu.addAction("Open JWDM")
        self.open_action.triggered.connect(self._main_window.bring_to_front)

        self.organize_action = self.menu.addAction("Organize folders")
        self.organize_action.setEnabled(False)

        self.status_action = self.menu.addAction(
            "Automatic organization: unavailable in Phase 0"
        )
        self.status_action.setEnabled(False)
        self.menu.addSeparator()

        self.exit_action = self.menu.addAction("Exit")
        self.exit_action.triggered.connect(self._application.quit)

        self.icon = QSystemTrayIcon(build_application_icon(), self._application)
        self.icon.setToolTip("JWDM")
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._handle_activation)

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

