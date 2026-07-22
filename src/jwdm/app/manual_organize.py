"""Coordinate the Phase 1 manual workflow without embedding services in widgets."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.history import HistoryError, HistoryRepository, OperationStatus
from jwdm.persistence.state import StateError
from jwdm.services.move_transaction import MoveError, MoveTransactionService
from jwdm.services.path_validation import PathValidationError
from jwdm.services.scan import ScanService
from jwdm.ui.main_window import MainWindow
from jwdm.ui.manual_dialogs import FolderSelectionDialog, HistoryDialog, ReviewDialog


class ManualOrganizeController:
    """Translate UI intents into scan, review, move, history, and undo services."""

    def __init__(
        self,
        window: MainWindow,
        scanner: ScanService,
        moves: MoveTransactionService,
        history: HistoryRepository,
    ) -> None:
        self._window = window
        self._scanner = scanner
        self._moves = moves
        self._history = history
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.manual")

        window.library_browse_requested.connect(self.choose_library)
        window.organize_requested.connect(self.start)
        window.history_requested.connect(self.show_history)
        window.undo_requested.connect(self.undo_last)
        self.refresh_activity()

    def choose_library(self) -> None:
        initial = str(self._window.library_path or Path.home())
        selected = QFileDialog.getExistingDirectory(
            self._window, "Choose organized library", initial
        )
        if selected:
            self._window.set_library_path(Path(selected))

    def start(self) -> None:
        library = self._window.library_path
        if library is None:
            self.choose_library()
            library = self._window.library_path
        if library is None:
            return

        sources = FolderSelectionDialog(self._window)
        if sources.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            plan = self._scanner.build_plan(sources.scan_roots(), library)
        except StateError as error:
            QMessageBox.critical(self._window, "Rules or settings unavailable", str(error))
            return
        except PathValidationError as error:
            QMessageBox.warning(self._window, "Unsafe folder selection", str(error))
            return
        except OSError as error:
            self._logger.error(
                "Manual scan failed",
                extra={"event": "scan_failed", "source": str(error)},
                exc_info=True,
            )
            QMessageBox.critical(self._window, "Scan failed", str(error))
            return

        if not plan.items and not plan.issues:
            QMessageBox.information(
                self._window, "Nothing found", "No files were found in the selected folders."
            )
            return

        review = ReviewDialog(plan, self._window)
        if review.exec() != QDialog.DialogCode.Accepted:
            return
        approved = review.selected_items()
        confirmation = QMessageBox.question(
            self._window,
            "Move approved files?",
            f"Move {len(approved)} approved files into {plan.library_root}?\n\n"
            "Existing files will never be overwritten. Name collisions keep both files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        results = self._moves.execute(plan.library_root, approved)
        succeeded = sum(result.succeeded for result in results)
        failed = len(results) - succeeded
        self._logger.info(
            "Manual plan completed",
            extra={
                "event": "manual_plan_completed",
                "count": len(results),
                "outcome": "completed" if failed == 0 else "partial",
            },
        )
        self.refresh_activity()
        details = "\n".join(
            f"• {result.source.name}: {result.message}"
            for result in results
            if not result.succeeded
        )
        message = f"Moved {succeeded} files."
        if failed:
            message += (
                f" {failed} files were refused or failed. Review their exact paths below.\n\n"
                f"{details}"
            )
            QMessageBox.warning(self._window, "Organization finished with issues", message)
        else:
            QMessageBox.information(self._window, "Organization complete", message)

    def show_history(self) -> None:
        try:
            operations = self._history.operations()
        except HistoryError as error:
            QMessageBox.critical(self._window, "History unavailable", str(error))
            return
        if not operations:
            QMessageBox.information(self._window, "Move history", "No moves are recorded yet.")
            return
        HistoryDialog(operations, self._window).exec()

    def undo_last(self) -> None:
        try:
            operation = self._history.latest_undoable()
        except HistoryError as error:
            QMessageBox.critical(self._window, "History unavailable", str(error))
            return
        if operation is None:
            QMessageBox.information(self._window, "Nothing to undo", "No completed move can be undone.")
            self.refresh_activity()
            return
        confirmation = QMessageBox.question(
            self._window,
            "Undo last move?",
            f"Restore:\n{operation.destination}\n\nto:\n{operation.source}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        try:
            self._moves.undo(operation)
        except MoveError as error:
            QMessageBox.warning(self._window, "Undo refused", str(error))
        else:
            QMessageBox.information(self._window, "Undo complete", "The file was restored.")
        self.refresh_activity()

    def refresh_activity(self) -> None:
        try:
            operations = self._history.operations()
            undoable = self._history.latest_undoable()
        except HistoryError as error:
            self._window.set_recent_activity(f"History error: {error}")
            self._window.set_undo_available(False)
            return
        self._window.set_undo_available(undoable is not None)
        if not operations:
            self._window.set_recent_activity("No manual organization activity yet.")
            return
        latest = operations[-1]
        action = "Restored" if latest.status is OperationStatus.UNDONE else latest.status.value.title()
        self._window.set_recent_activity(
            f"{action}: {latest.source.name} → {latest.destination}"
        )
