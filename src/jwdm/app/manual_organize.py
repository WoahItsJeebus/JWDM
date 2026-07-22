"""Coordinate manual scan, review, rules, move, history, and undo workflows."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.history import HistoryError, HistoryRepository, OperationStatus
from jwdm.persistence.state import StateError
from jwdm.pipeline.models import ScanPlan, ScanProgress, ScanRoot, ScanStage
from jwdm.services.move_transaction import MoveError, MoveTransactionService
from jwdm.services.path_validation import PathValidationError
from jwdm.services.rule_suggestions import RuleSuggestionError, RuleSuggestionService
from jwdm.services.scan import ScanService
from jwdm.ui.main_window import MainWindow
from jwdm.ui.manual_dialogs import FolderSelectionDialog, HistoryDialog, ReviewDialog


class _ScanSignals(QObject):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)


class ManualOrganizeController(QObject):
    """Translate UI intents into scan, review, move, history, and undo services."""

    def __init__(
        self,
        window: MainWindow,
        scanner: ScanService,
        moves: MoveTransactionService,
        history: HistoryRepository,
        rule_suggestions: RuleSuggestionService | None = None,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._scanner = scanner
        self._moves = moves
        self._history = history
        self._rule_suggestions = rule_suggestions
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.manual")
        self._scan_signals = _ScanSignals(self)
        self._scan_signals.progress.connect(self._handle_scan_progress)
        self._scan_signals.completed.connect(self._handle_scan_completed)
        self._scan_signals.failed.connect(self._handle_scan_failed)
        self._scan_thread: threading.Thread | None = None
        self._scan_in_progress = False

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
        if self._scan_in_progress:
            return
        library = self._window.library_path
        if library is None:
            self.choose_library()
            library = self._window.library_path
        if library is None:
            return

        sources = FolderSelectionDialog(self._window)
        if sources.exec() != QDialog.DialogCode.Accepted:
            return

        self._begin_scan(sources.scan_roots(), library)

    @property
    def scan_in_progress(self) -> bool:
        return self._scan_in_progress

    def _begin_scan(self, roots: tuple[ScanRoot, ...], library: Path) -> None:
        self._scan_in_progress = True
        self._window.set_manual_scan_state(
            "Manual scan: discovering files", active=True
        )
        self._logger.info(
            "Manual scan started",
            extra={
                "event": "manual_scan_started",
                "count": len(roots),
                "destination": str(library),
            },
        )
        self._scan_thread = threading.Thread(
            target=self._run_scan,
            args=(roots, library),
            daemon=True,
            name="JWDM manual scan",
        )
        self._scan_thread.start()

    def _run_scan(self, roots: tuple[ScanRoot, ...], library: Path) -> None:
        try:
            plan = self._scanner.build_plan(
                roots, library, self._scan_signals.progress.emit
            )
        except Exception as error:
            self._logger.error(
                "Manual scan failed",
                extra={
                    "event": "scan_failed",
                    "error_type": type(error).__name__,
                },
                exc_info=True,
            )
            self._scan_signals.failed.emit(error)
        else:
            self._scan_signals.completed.emit(plan)

    @Slot(object)
    def _handle_scan_progress(self, progress: object) -> None:
        if not isinstance(progress, ScanProgress):
            return
        if progress.stage is ScanStage.DISCOVERING:
            self._window.set_manual_scan_state(
                f"Manual scan: discovering \u2014 {progress.completed_items} files found",
                active=True,
            )
            return
        total = progress.total_items or 0
        self._window.set_manual_scan_state(
            "Manual scan: analyzing "
            f"{progress.completed_items} of {total} \u2014 {progress.current_path.name}",
            active=True,
            completed=progress.completed_items,
            total=total,
        )

    @Slot(object)
    def _handle_scan_failed(self, error: object) -> None:
        self._scan_in_progress = False
        self._scan_thread = None
        self._window.set_manual_scan_state(
            "Manual scan: stopped because of an error", active=False
        )
        if isinstance(error, StateError):
            QMessageBox.critical(
                self._window, "Rules or settings unavailable", str(error)
            )
        elif isinstance(error, PathValidationError):
            QMessageBox.warning(self._window, "Unsafe folder selection", str(error))
        elif isinstance(error, OSError):
            QMessageBox.critical(self._window, "Scan failed", str(error))
        else:
            QMessageBox.critical(
                self._window,
                "Scan failed",
                "The scan stopped unexpectedly. Details were written to the JWDM log.",
            )

    @Slot(object)
    def _handle_scan_completed(self, result: object) -> None:
        if not isinstance(result, ScanPlan):
            self._handle_scan_failed(TypeError("Scan returned an invalid result."))
            return
        self._scan_in_progress = False
        self._scan_thread = None
        total = len(result.items)
        self._window.set_manual_scan_state(
            f"Manual scan: complete \u2014 {total} files",
            active=False,
            completed=total,
            total=total,
        )
        self._logger.info(
            "Manual scan completed",
            extra={
                "event": "manual_scan_completed",
                "count": total,
                "issue_count": len(result.issues),
            },
        )
        self._review_plan(result)

    def _review_plan(self, plan: ScanPlan) -> None:

        if not plan.items and not plan.issues:
            QMessageBox.information(
                self._window, "Nothing found", "No files were found in the selected folders."
            )
            return

        review = ReviewDialog(plan, self._window)
        if review.exec() != QDialog.DialogCode.Accepted:
            return
        approved = review.selected_items()
        try:
            suggestions = (
                self._rule_suggestions.suggestions(review.corrections())
                if self._rule_suggestions is not None
                else ()
            )
        except RuleSuggestionError as error:
            QMessageBox.warning(self._window, "Rule suggestion conflict", str(error))
            return
        rule_note = (
            f"\n\n{len(suggestions)} extension rule(s) will be created or updated."
            if suggestions
            else ""
        )
        confirmation = QMessageBox.question(
            self._window,
            "Move approved files?",
            f"Move {len(approved)} approved files into {plan.library_root}?\n\n"
            "Existing files will never be overwritten. Name collisions keep both files."
            f"{rule_note}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        if self._rule_suggestions is not None:
            try:
                self._rule_suggestions.save(suggestions)
            except StateError as error:
                QMessageBox.critical(
                    self._window,
                    "Rules were not saved",
                    "No files were moved because the requested rules could not be saved."
                    f"\n\n{error}",
                )
                return
            if suggestions:
                self._logger.info(
                    "Review corrections saved as rules",
                    extra={
                        "event": "correction_rules_saved",
                        "count": len(suggestions),
                    },
                )

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
