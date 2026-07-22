"""JWDM process entry point."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication, QMessageBox

from jwdm import __version__
from jwdm.logging_config import APPLICATION_LOGGER, configure_logging
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

    main_window = MainWindow()
    tray = TrayController(application, main_window)
    tray_available = tray.show()
    application.setQuitOnLastWindowClosed(not tray_available)
    main_window.show()

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
