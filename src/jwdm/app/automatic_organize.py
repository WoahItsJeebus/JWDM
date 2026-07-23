"""Qt coordination for automatic organizer controls and candidate review."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox

from jwdm.config import AppSettings
from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.state import StateError
from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState
from jwdm.services.automatic_organizer import AutomaticOrganizer
from jwdm.services.path_validation import PathValidationError
from jwdm.services.volumes import DestinationStatus
from jwdm.ui.main_window import MainWindow
from jwdm.ui.candidate_dialogs import CandidateReviewDialog
from jwdm.ui.tray import TrayController
from jwdm.watcher.directory_watcher import WatcherError


class _CandidateBridge(QObject):
    changed = Signal(object)


class _DestinationBridge(QObject):
    changed = Signal(object)


class AutomaticOrganizeController:
    """Keep Qt widgets on the UI thread while the readiness worker runs separately."""

    def __init__(
        self,
        window: MainWindow,
        organizer: AutomaticOrganizer,
        history_refresh: Callable[[], None],
        settings_provider: Callable[[], AppSettings] | None = None,
        rule_editor: Callable[[Path], bool] | None = None,
    ) -> None:
        self._window = window
        self._organizer = organizer
        self._history_refresh = history_refresh
        self._settings_provider = settings_provider or AppSettings
        self._rule_editor = rule_editor
        self._tray: TrayController | None = None
        self._known_moved: set[str] = set()
        self._bridge = _CandidateBridge()
        self._bridge.changed.connect(self._apply_candidates)
        self._organizer.subscribe(self._bridge.changed.emit)
        self._destination_bridge = _DestinationBridge()
        self._destination_bridge.changed.connect(self._apply_destination)
        self._organizer.subscribe_destination(self._destination_bridge.changed.emit)
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.automatic_ui")

        window.incoming_browse_requested.connect(self.choose_incoming)
        window.automatic_toggle_requested.connect(self.toggle_running)
        window.automatic_pause_requested.connect(self.toggle_paused)
        window.candidate_review_requested.connect(self.review_candidate)
        window.candidate_rule_requested.connect(self.quick_add_rule)
        self._apply_candidates(())

    def set_tray(self, tray: TrayController) -> None:
        self._tray = tray
        tray.bind_automatic(self.pause, self.resume)
        self._update_controls(self._organizer.snapshots())

    def choose_incoming(self) -> None:
        if self._organizer.is_running:
            return
        initial = str(self._window.incoming_path or Path.home())
        selected = QFileDialog.getExistingDirectory(
            self._window, "Choose incoming folder", initial
        )
        if selected:
            selected_path = Path(selected)
            paths = self._window.incoming_paths
            if not any(
                path.resolve(strict=False) == selected_path.resolve(strict=False)
                for path in paths
            ):
                self._window.set_incoming_paths((*paths, selected_path))

    def toggle_running(self) -> None:
        if self._organizer.is_running:
            self.stop()
            return
        incoming = self._window.incoming_paths
        library = self._window.library_path
        if not incoming:
            self.choose_incoming()
            incoming = self._window.incoming_paths
        if library is None:
            self._window.library_browse_requested.emit()
            library = self._window.library_path
        if not incoming or library is None:
            return
        try:
            self._organizer.start(
                incoming,
                library,
                process_existing=self._settings_provider().process_existing_on_start,
            )
        except (
            PathValidationError,
            WatcherError,
            StateError,
            OSError,
            RuntimeError,
        ) as error:
            QMessageBox.warning(self._window, "Automatic organization unavailable", str(error))
        self._update_controls(self._organizer.snapshots())

    def start_if_configured(self) -> None:
        settings = self._settings_provider()
        if (
            settings.start_automatic
            and self._window.incoming_paths
            and self._window.library_path is not None
            and not self._organizer.is_running
        ):
            self.toggle_running()

    def review_candidate(self, candidate: object) -> None:
        if not isinstance(candidate, CandidateSnapshot):
            return
        current = self._organizer.snapshot(candidate.candidate_id)
        if current is None:
            QMessageBox.information(
                self._window,
                "Candidate no longer available",
                "This candidate is no longer in the automatic review queue.",
            )
            return
        dialog = CandidateReviewDialog(current, self._window)
        dialog.exec()
        if dialog.add_rule_requested:
            self.quick_add_rule(current)

    def quick_add_rule(self, candidate: object) -> None:
        if not isinstance(candidate, CandidateSnapshot) or self._rule_editor is None:
            return
        self._rule_editor(candidate.source_path)

    def stop(self) -> None:
        try:
            self._organizer.stop()
        except (WatcherError, RuntimeError) as error:
            QMessageBox.warning(self._window, "Automatic stop issue", str(error))
        self._update_controls(self._organizer.snapshots())

    def toggle_paused(self) -> None:
        if self._organizer.is_paused:
            self.resume()
        else:
            self.pause()

    def pause(self) -> None:
        try:
            self._organizer.pause()
        except RuntimeError as error:
            QMessageBox.information(self._window, "Automatic organization", str(error))
        self._update_controls(self._organizer.snapshots())

    def resume(self) -> None:
        try:
            self._organizer.resume()
        except RuntimeError as error:
            QMessageBox.information(self._window, "Automatic organization", str(error))
        self._update_controls(self._organizer.snapshots())

    def shutdown(self) -> None:
        if not self._organizer.is_running:
            return
        try:
            self._organizer.stop()
        except (WatcherError, RuntimeError):
            self._logger.exception(
                "Automatic organizer did not stop cleanly during shutdown",
                extra={"event": "automatic_shutdown_error"},
            )

    def _apply_candidates(self, candidates: object) -> None:
        typed_candidates = tuple(candidates) if isinstance(candidates, tuple) else ()
        self._window.set_candidates(typed_candidates)
        moved = {
            candidate.candidate_id
            for candidate in typed_candidates
            if isinstance(candidate, CandidateSnapshot)
            and candidate.state is CandidateState.MOVED
        }
        if moved - self._known_moved:
            self._history_refresh()
        self._known_moved.update(moved)
        self._update_controls(typed_candidates)

    def _update_controls(self, candidates: tuple[CandidateSnapshot, ...]) -> None:
        running = self._organizer.is_running
        paused = self._organizer.is_paused
        self._window.set_automatic_state(running, paused)
        if self._tray is not None:
            pending = sum(
                candidate.state
                not in {CandidateState.MOVED, CandidateState.FAILED, CandidateState.EXCLUDED}
                for candidate in candidates
            )
            review = sum(
                candidate.state is CandidateState.NEEDS_REVIEW for candidate in candidates
            )
            self._tray.set_automatic_state(running, paused, pending, review)

    def _apply_destination(self, status: object) -> None:
        if not isinstance(status, DestinationStatus):
            return
        self._window.set_destination_status(status.available, status.detail)
        if self._tray is not None:
            self._tray.set_destination_status(status.available, status.detail)
