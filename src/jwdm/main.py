"""JWDM process entry point."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from jwdm import __version__
from jwdm.app.automatic_organize import AutomaticOrganizeController
from jwdm.app.manual_organize import ManualOrganizeController
from jwdm.app.settings import SettingsController
from jwdm.classification.rule_classifier import RuleClassifier
from jwdm.logging_config import APPLICATION_LOGGER, configure_logging
from jwdm.persistence.history import HistoryRepository
from jwdm.persistence.state import StateError, StateRepository
from jwdm.services.automatic_organizer import AutomaticOrganizer
from jwdm.services.exclusions import ExclusionMatcher
from jwdm.services.move_transaction import MoveTransactionService
from jwdm.services.operation_suppression import OperationSuppressor
from jwdm.services.scan import ScanService
from jwdm.services.startup import StartupError, StartupManager
from jwdm.ui.icons import build_application_icon
from jwdm.ui.main_window import MainWindow
from jwdm.ui.tray import TrayController


def _install_exception_hooks(logger: logging.Logger) -> None:
    """Capture otherwise-unhandled main-thread and worker-thread failures."""

    def report_exception(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: object,
    ) -> None:
        logger.critical(
            "Unhandled application exception",
            extra={"event": "unhandled_exception"},
            exc_info=(exception_type, exception, traceback),
        )
        application = QApplication.instance()
        if application is not None:
            QMessageBox.critical(
                None,
                "JWDM encountered an error",
                "JWDM encountered an unexpected error. Details were written to the log.",
            )

    def report_thread_exception(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "Unhandled worker-thread exception",
            extra={"event": "unhandled_thread_exception"},
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = report_exception
    threading.excepthook = report_thread_exception


def run(arguments: Sequence[str] | None = None) -> int:
    """Start JWDM and return the Qt event-loop status code."""

    log_path = configure_logging()
    logger = logging.getLogger(APPLICATION_LOGGER)
    _install_exception_hooks(logger)
    logger.info(
        "JWDM starting",
        extra={"event": "application_start"},
    )

    application_arguments = list(arguments) if arguments is not None else sys.argv
    application = QApplication(application_arguments)
    application.setApplicationName("JWDM")
    application.setApplicationDisplayName("JWDM")
    application.setApplicationVersion(__version__)
    application.setOrganizationName("JWDM")
    application.setWindowIcon(build_application_icon())

    try:
        state = StateRepository()
        settings = state.settings()
    except StateError as error:
        logger.critical(
            "Persistent state is unavailable",
            extra={"event": "state_startup_error"},
            exc_info=True,
        )
        QMessageBox.critical(
            None,
            "JWDM state is unavailable",
            f"JWDM could not safely open its settings database. No files were moved.\n\n{error}",
        )
        return 1

    main_window = MainWindow()
    startup = StartupManager.for_current_process()
    settings_controller = SettingsController(
        application,
        main_window,
        state,
        startup,
        settings,
    )
    try:
        settings_controller.synchronize_startup()
    except StartupError as error:
        logger.error(
            "Windows startup entry could not be synchronized",
            extra={"event": "startup_sync_error"},
            exc_info=True,
        )
        QMessageBox.warning(main_window, "Start with Windows", str(error))

    history = HistoryRepository()
    suppressor = OperationSuppressor()
    moves = MoveTransactionService(history, suppressor)
    classifier = RuleClassifier(state)
    exclusions = ExclusionMatcher(
        lambda: settings_controller.current().exclusions
    )
    manual_controller = ManualOrganizeController(
        main_window,
        ScanService(classifier=classifier, exclusion_matcher=exclusions),
        moves,
        history,
    )
    automatic_service = AutomaticOrganizer(
        moves,
        suppressor,
        classifier=classifier,
        exclusions=exclusions,
        state_repository=state,
        confidence_policy=lambda: settings_controller.current().confidence_policy,
    )
    automatic_controller = AutomaticOrganizeController(
        main_window,
        automatic_service,
        manual_controller.refresh_activity,
        settings_controller.current,
    )
    tray = TrayController(application, main_window, manual_controller.start)
    automatic_controller.set_tray(tray)
    application.aboutToQuit.connect(automatic_controller.shutdown)
    tray_available = tray.show()
    settings_controller.set_tray(tray, tray_available)
    application.setQuitOnLastWindowClosed(not tray_available)
    launch_minimized = (
        "--minimized" in application_arguments
        or settings_controller.current().launch_minimized
    )
    if not launch_minimized or not tray_available:
        main_window.show()
    QTimer.singleShot(0, automatic_controller.start_if_configured)

    logger.info(
        "JWDM user interface ready",
        extra={"event": "application_ready"},
    )
    logger.info(
        f"Structured log active at {log_path}",
        extra={"event": "logging_ready"},
    )
    exit_code = application.exec()
    logger.info(
        "JWDM stopped",
        extra={"event": "application_stop"},
    )
    return exit_code


def main() -> int:
    """Console-script compatible entry point."""

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
