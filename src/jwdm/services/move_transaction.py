"""Conservative, journaled Phase 1 move and undo transactions."""

from __future__ import annotations

import logging
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.history import (
    HistoryError,
    HistoryOperation,
    HistoryRepository,
    OperationStatus,
)
from jwdm.pipeline.models import PlanItem, PlanItemStatus
from jwdm.services.destinations import destination_for, resolve_collision


class MoveError(RuntimeError):
    """An approved move or undo could not be completed safely."""


class MoveEventSuppressor(Protocol):
    def suppress(self, source: Path, destination: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class MoveResult:
    operation_id: str | None
    source: Path
    destination: Path | None
    succeeded: bool
    message: str


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


class MoveTransactionService:
    """Execute approved moves only after snapshot revalidation and durable intent."""

    def __init__(
        self,
        history: HistoryRepository,
        event_suppressor: MoveEventSuppressor | None = None,
    ) -> None:
        self._history = history
        self._event_suppressor = event_suppressor
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.moves")

    def execute(self, library_root: Path, items: tuple[PlanItem, ...]) -> tuple[MoveResult, ...]:
        return tuple(self._move_one(library_root, item) for item in items)

    def undo(self, operation: HistoryOperation) -> MoveResult:
        if operation.status not in {OperationStatus.COMPLETED, OperationStatus.UNDO_FAILED}:
            raise MoveError(f"Operation {operation.operation_id} is not undoable.")
        if operation.destination_modified_ns is None:
            raise MoveError("Undo record has no destination snapshot.")

        destination = operation.destination
        source = operation.source
        self._validate_current_file(
            destination,
            operation.size,
            operation.destination_modified_ns,
            "Moved file changed after the original operation; undo was refused.",
        )
        if source.exists():
            raise MoveError(f"Original path is occupied; undo will not overwrite it: {source}")
        if not source.parent.exists() or not source.parent.is_dir():
            raise MoveError(f"Original parent folder is unavailable: {source.parent}")
        if _is_link_or_junction(source.parent):
            raise MoveError(f"Original parent folder became a link or junction: {source.parent}")
        if not self._same_volume(destination, source.parent):
            raise MoveError("Cross-volume undo is deferred until Phase 4; no files were changed.")

        try:
            self._history.record_undo_intended(operation.operation_id)
        except HistoryError as error:
            raise MoveError(f"Undo was not started because history could not be written: {error}") from error
        try:
            if self._event_suppressor is not None:
                self._event_suppressor.suppress(destination, source)
            destination.rename(source)
            restored = source.stat(follow_symlinks=False)
            if restored.st_size != operation.size:
                raise MoveError("Undo verification failed because the restored size differs.")
            self._history.record_undo_completed(operation.operation_id)
            self._logger.info(
                "Move undone",
                extra={
                    "event": "move_undone",
                    "operation_id": operation.operation_id,
                    "source": str(destination),
                    "destination": str(source),
                    "outcome": "completed",
                },
            )
            return MoveResult(
                operation.operation_id,
                destination,
                source,
                True,
                "Undo completed",
            )
        except (OSError, MoveError, HistoryError) as error:
            message = str(error)
            if not isinstance(error, HistoryError):
                try:
                    self._history.record_undo_failed(operation.operation_id, message)
                except HistoryError as history_error:
                    message = f"{message}; undo history also could not be updated: {history_error}"
            else:
                message = (
                    f"{message}; history remains pending, so inspect both paths before taking "
                    "further action"
                )
            self._logger.error(
                "Undo failed",
                extra={
                    "event": "undo_failed",
                    "operation_id": operation.operation_id,
                    "source": str(destination),
                    "destination": str(source),
                    "outcome": "failed",
                },
                exc_info=True,
            )
            raise MoveError(message) from error

    def _move_one(self, library_root: Path, item: PlanItem) -> MoveResult:
        operation_id: str | None = None
        destination: Path | None = None
        intention_recorded = False
        try:
            if item.status is not PlanItemStatus.READY or item.category is None:
                raise MoveError("Only reviewed, ready plan items can be moved.")
            self._validate_current_file(
                item.source,
                item.size,
                item.modified_ns,
                "Source changed after the preview; rescan before moving it.",
            )
            resolved_library = library_root.resolve(strict=True)
            if not resolved_library.is_dir() or _is_link_or_junction(library_root):
                raise MoveError(f"Library folder is unavailable or unsafe: {library_root}")
            if not self._same_volume(item.source, resolved_library):
                raise MoveError(
                    "Cross-volume moves are deferred until Phase 4; the source was left in place."
                )

            base_destination = destination_for(resolved_library, item.category, item.source.name)
            base_destination.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_free_space(base_destination.parent, item.size)
            destination, collision_behavior = resolve_collision(base_destination)
            operation_id = str(uuid.uuid4())
            operation = HistoryOperation(
                operation_id=operation_id,
                source=item.source,
                destination=destination,
                size=item.size,
                source_modified_ns=item.modified_ns,
                category=item.category,
                reason=item.reason,
                collision_behavior=collision_behavior,
                status=OperationStatus.PENDING,
                planned_at=datetime.now(UTC),
            )
            self._history.record_move_intended(operation)
            intention_recorded = True
            self._validate_current_file(
                item.source,
                item.size,
                item.modified_ns,
                "Source changed while the move was being prepared; it was left in place.",
            )

            if self._event_suppressor is not None:
                self._event_suppressor.suppress(item.source, destination)
            item.source.rename(destination)

            destination_stat = destination.stat(follow_symlinks=False)
            if destination_stat.st_size != item.size:
                rolled_back = self._attempt_same_volume_rollback(item.source, destination)
                location = "original path" if rolled_back else f"destination {destination}"
                raise MoveError(
                    f"Destination verification failed because its size differs; file remains at {location}."
                )
            self._history.record_move_completed(operation_id, destination_stat.st_mtime_ns)
            self._logger.info(
                "Move completed",
                extra={
                    "event": "move_completed",
                    "operation_id": operation_id,
                    "source": str(item.source),
                    "destination": str(destination),
                    "category": item.category,
                    "outcome": "completed",
                },
            )
            return MoveResult(operation_id, item.source, destination, True, "Move completed")
        except (OSError, MoveError, ValueError, HistoryError) as error:
            message = str(error)
            if operation_id is not None and intention_recorded and not isinstance(error, HistoryError):
                try:
                    self._history.record_move_failed(operation_id, message)
                except HistoryError as history_error:
                    message = f"{message}; move history also could not be updated: {history_error}"
            elif intention_recorded and isinstance(error, HistoryError):
                message = (
                    f"{message}; history remains pending, so inspect source {item.source} and "
                    f"destination {destination} before taking further action"
                )
            self._logger.error(
                "Move refused or failed",
                extra={
                    "event": "move_failed",
                    "operation_id": operation_id or "not_started",
                    "source": str(item.source),
                    "destination": str(destination) if destination else "",
                    "outcome": "failed",
                },
                exc_info=True,
            )
            return MoveResult(operation_id, item.source, destination, False, message)

    @staticmethod
    def _validate_current_file(
        path: Path, expected_size: int, expected_modified_ns: int, changed_message: str
    ) -> None:
        if _is_link_or_junction(path):
            raise MoveError(f"Linked paths are not moved: {path}")
        try:
            current = path.stat(follow_symlinks=False)
        except FileNotFoundError as error:
            raise MoveError(f"File no longer exists: {path}") from error
        if not stat.S_ISREG(current.st_mode):
            raise MoveError(f"Path is no longer a regular file: {path}")
        if current.st_size != expected_size or current.st_mtime_ns != expected_modified_ns:
            raise MoveError(changed_message)

    @staticmethod
    def _same_volume(path: Path, directory: Path) -> bool:
        return path.stat(follow_symlinks=False).st_dev == directory.stat().st_dev

    @staticmethod
    def _ensure_free_space(directory: Path, required_bytes: int) -> None:
        try:
            available = shutil.disk_usage(directory).free
        except OSError as error:
            raise MoveError(f"Cannot check free space for {directory}: {error}") from error
        if available < required_bytes:
            raise MoveError(
                f"Destination has insufficient free space: {available} available, {required_bytes} required."
            )

    @staticmethod
    def _attempt_same_volume_rollback(source: Path, destination: Path) -> bool:
        if source.exists() or not destination.exists():
            return False
        try:
            destination.rename(source)
        except OSError:
            return False
        return True
