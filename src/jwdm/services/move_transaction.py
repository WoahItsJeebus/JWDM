"""Journaled same-volume and verified cross-volume move transactions."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.history import (
    HistoryError,
    HistoryOperation,
    HistoryRepository,
    OperationStatus,
)
from jwdm.pipeline.models import PlanItem, PlanItemStatus
from jwdm.services.destinations import destination_for, resolve_collision
from jwdm.services.volumes import VolumeService


class MoveError(RuntimeError):
    """An approved move, undo, or recovery could not be completed safely."""


class MoveEventSuppressor(Protocol):
    def suppress(self, source: Path, destination: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class MoveResult:
    operation_id: str | None
    source: Path
    destination: Path | None
    succeeded: bool
    message: str


TransferDirection = Literal["move", "undo"]


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


class MoveTransactionService:
    """Never remove a cross-volume source until a durable verified-copy checkpoint."""

    _COPY_BUFFER_SIZE = 1024 * 1024

    def __init__(
        self,
        history: HistoryRepository,
        event_suppressor: MoveEventSuppressor | None = None,
        volume_service: VolumeService | None = None,
    ) -> None:
        self._history = history
        self._event_suppressor = event_suppressor
        self._volumes = volume_service or VolumeService()
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.moves")

    def execute(self, library_root: Path, items: tuple[PlanItem, ...]) -> tuple[MoveResult, ...]:
        return tuple(self._move_one(library_root, item) for item in items)

    def recover_pending(self) -> tuple[MoveResult, ...]:
        """Resolve journaled crash windows without overwriting either user path."""

        try:
            pending = self._history.pending_operations()
        except HistoryError as error:
            raise MoveError(f"Pending operations cannot be inspected: {error}") from error
        results: list[MoveResult] = []
        for operation in pending:
            try:
                result = self._recover_operation(
                    operation,
                    "JWDM stopped before the transaction finished.",
                )
            except (HistoryError, MoveError, OSError) as error:
                message = f"Automatic recovery could not resolve the operation: {error}"
                try:
                    self._history.record_recovery_required(
                        operation.operation_id, message
                    )
                except HistoryError as history_error:
                    message = f"{message}; recovery status could not be journaled: {history_error}"
                result = MoveResult(
                    operation.operation_id,
                    operation.source,
                    operation.destination,
                    False,
                    message,
                )
                self._logger.error(
                    "Pending operation requires manual recovery",
                    extra={
                        "event": "operation_recovery_required",
                        "operation_id": operation.operation_id,
                        "source": str(operation.source),
                        "destination": str(operation.destination),
                        "outcome": "recovery_required",
                    },
                    exc_info=True,
                )
            results.append(result)
        return tuple(results)

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

        cross_volume = not self._volumes.same_volume(destination, source.parent)
        temporary = (
            self._temporary_path(source.parent, operation.operation_id, "undo")
            if cross_volume
            else None
        )
        if cross_volume:
            self._ensure_free_space(source.parent, operation.size)
        try:
            self._history.record_undo_intended(
                operation.operation_id,
                cross_volume=cross_volume,
                temporary_path=temporary,
            )
        except HistoryError as error:
            raise MoveError(f"Undo was not started because history could not be written: {error}") from error

        try:
            if self._event_suppressor is not None:
                self._event_suppressor.suppress(destination, source)
            if cross_volume:
                assert temporary is not None
                self._copy_verify_publish_remove(
                    operation.operation_id,
                    destination,
                    source,
                    temporary,
                    operation.size,
                    operation.destination_modified_ns,
                    "undo",
                )
            else:
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
            return MoveResult(operation.operation_id, destination, source, True, "Undo completed")
        except (OSError, MoveError, HistoryError) as error:
            recovered = self._attempt_recorded_recovery(operation.operation_id, str(error))
            if recovered is not None and recovered.succeeded:
                return recovered
            message = recovered.message if recovered is not None else str(error)
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

            base_destination = destination_for(resolved_library, item.category, item.source.name)
            base_destination.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_free_space(base_destination.parent, item.size)
            destination, collision_behavior = resolve_collision(base_destination)
            cross_volume = not self._volumes.same_volume(item.source, resolved_library)
            operation_id = str(uuid.uuid4())
            temporary = (
                self._temporary_path(destination.parent, operation_id, "move")
                if cross_volume
                else None
            )
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
                cross_volume=cross_volume,
                temporary_path=temporary,
                source_volume_id=self._volumes.identity(item.source),
                destination_volume_id=self._volumes.identity(resolved_library),
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
            if cross_volume:
                assert temporary is not None
                self._copy_verify_publish_remove(
                    operation_id,
                    item.source,
                    destination,
                    temporary,
                    item.size,
                    item.modified_ns,
                    "move",
                )
            else:
                item.source.rename(destination)

            destination_stat = destination.stat(follow_symlinks=False)
            if destination_stat.st_size != item.size:
                if not cross_volume:
                    rolled_back = self._attempt_same_volume_rollback(
                        item.source, destination
                    )
                    location = "original path" if rolled_back else f"destination {destination}"
                else:
                    location = f"destination {destination}"
                raise MoveError(
                    "Destination verification failed because its size differs; "
                    f"file remains at {location}."
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
            message = "Verified cross-volume move completed" if cross_volume else "Move completed"
            return MoveResult(operation_id, item.source, destination, True, message)
        except (OSError, MoveError, ValueError, HistoryError) as error:
            if operation_id is not None and intention_recorded:
                recovered = self._attempt_recorded_recovery(operation_id, str(error))
                if recovered is not None:
                    return recovered
            message = str(error)
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

    def _copy_verify_publish_remove(
        self,
        operation_id: str,
        source: Path,
        destination: Path,
        temporary: Path,
        expected_size: int,
        expected_modified_ns: int,
        direction: TransferDirection,
    ) -> None:
        copy_checkpoint = False
        try:
            source_hash = hashlib.sha256()
            copied = 0
            with source.open("rb") as source_stream, temporary.open("xb") as target_stream:
                while chunk := source_stream.read(self._COPY_BUFFER_SIZE):
                    target_stream.write(chunk)
                    source_hash.update(chunk)
                    copied += len(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            if copied != expected_size:
                raise MoveError(
                    f"Copy size changed during transfer: expected {expected_size}, copied {copied}."
                )
            self._validate_current_file(
                source,
                expected_size,
                expected_modified_ns,
                "Source changed during cross-volume copy; it was not removed.",
            )
            temporary_hash = self._sha256(temporary)
            digest = source_hash.hexdigest()
            if temporary_hash != digest:
                raise MoveError("Cross-volume SHA-256 verification failed; source was not removed.")
            self._history.record_copy_verified(
                operation_id,
                digest,
                direction=direction,
            )
            copy_checkpoint = True
            temporary.rename(destination)
            self._validate_current_file(
                source,
                expected_size,
                expected_modified_ns,
                "Source changed after copy verification; it was not removed.",
            )
            self._verify_hash(source, expected_size, digest)
            source.unlink()
            self._history.record_source_removed(operation_id, direction=direction)
        except (OSError, MoveError, HistoryError):
            if not copy_checkpoint:
                self._discard_temporary(temporary)
            raise

    def _attempt_recorded_recovery(
        self, operation_id: str, failure_reason: str
    ) -> MoveResult | None:
        operation: HistoryOperation | None = None
        try:
            operation = self._history.operation(operation_id)
            if operation.status not in {
                OperationStatus.PENDING,
                OperationStatus.UNDO_PENDING,
            }:
                return MoveResult(
                    operation_id,
                    operation.source,
                    operation.destination,
                    operation.status in {OperationStatus.COMPLETED, OperationStatus.UNDONE},
                    operation.error or failure_reason,
                )
            return self._recover_operation(operation, failure_reason)
        except (HistoryError, MoveError, OSError) as recovery_error:
            message = f"{failure_reason}; recorded recovery also failed: {recovery_error}"
            try:
                self._history.record_recovery_required(operation_id, message)
            except HistoryError:
                return None
            return MoveResult(
                operation_id,
                operation.source if operation is not None else Path(),
                operation.destination if operation is not None else None,
                False,
                message,
            )

    def _recover_operation(
        self, operation: HistoryOperation, failure_reason: str
    ) -> MoveResult:
        if operation.status is OperationStatus.PENDING:
            return self._recover_move(operation, failure_reason)
        if operation.status is OperationStatus.UNDO_PENDING:
            return self._recover_undo(operation, failure_reason)
        raise MoveError(f"Operation {operation.operation_id} is not pending recovery.")

    def _recover_move(
        self, operation: HistoryOperation, failure_reason: str
    ) -> MoveResult:
        if operation.cross_volume:
            return self._recover_cross_transfer(
                operation,
                operation.source,
                operation.destination,
                operation.temporary_path,
                operation.copy_verified,
                operation.source_removed,
                "move",
                failure_reason,
            )
        source_exists = operation.source.is_file()
        destination_exists = operation.destination.is_file()
        if destination_exists and not source_exists:
            destination_stat = operation.destination.stat(follow_symlinks=False)
            if destination_stat.st_size != operation.size:
                raise MoveError("Recovered destination size does not match the move intention.")
            self._history.record_move_completed(
                operation.operation_id, destination_stat.st_mtime_ns
            )
            return MoveResult(
                operation.operation_id,
                operation.source,
                operation.destination,
                True,
                "Same-volume move recovered",
            )
        if source_exists and not destination_exists:
            self._history.record_move_failed(operation.operation_id, failure_reason)
            return MoveResult(
                operation.operation_id,
                operation.source,
                operation.destination,
                False,
                f"Move did not mutate the source: {failure_reason}",
            )
        raise MoveError("Both or neither move path exists; JWDM left all paths unchanged.")

    def _recover_undo(
        self, operation: HistoryOperation, failure_reason: str
    ) -> MoveResult:
        if operation.undo_cross_volume:
            return self._recover_cross_transfer(
                operation,
                operation.destination,
                operation.source,
                operation.undo_temporary_path,
                operation.undo_copy_verified,
                operation.undo_source_removed,
                "undo",
                failure_reason,
            )
        destination_exists = operation.destination.is_file()
        source_exists = operation.source.is_file()
        if source_exists and not destination_exists:
            restored = operation.source.stat(follow_symlinks=False)
            if restored.st_size != operation.size:
                raise MoveError("Recovered undo size does not match the history record.")
            self._history.record_undo_completed(operation.operation_id)
            return MoveResult(
                operation.operation_id,
                operation.destination,
                operation.source,
                True,
                "Same-volume undo recovered",
            )
        if destination_exists and not source_exists:
            self._history.record_undo_failed(operation.operation_id, failure_reason)
            return MoveResult(
                operation.operation_id,
                operation.destination,
                operation.source,
                False,
                f"Undo did not mutate the moved file: {failure_reason}",
            )
        raise MoveError("Both or neither undo path exists; JWDM left all paths unchanged.")

    def _recover_cross_transfer(
        self,
        operation: HistoryOperation,
        transfer_source: Path,
        transfer_destination: Path,
        temporary: Path | None,
        copy_verified: bool,
        source_removed: bool,
        direction: TransferDirection,
        failure_reason: str,
    ) -> MoveResult:
        if temporary is None:
            raise MoveError("Cross-volume recovery record has no temporary path.")
        self._validate_temporary_path(
            temporary,
            transfer_destination,
            operation.operation_id,
            direction,
        )
        if not copy_verified or operation.content_hash is None:
            if transfer_source.is_file() and not transfer_destination.exists():
                self._discard_temporary(temporary)
                if direction == "move":
                    self._history.record_move_failed(operation.operation_id, failure_reason)
                else:
                    self._history.record_undo_failed(operation.operation_id, failure_reason)
                return MoveResult(
                    operation.operation_id,
                    transfer_source,
                    transfer_destination,
                    False,
                    f"Unverified partial copy was discarded; source remains: {failure_reason}",
                )
            raise MoveError("Cross-volume copy has no durable verification checkpoint.")

        if not transfer_destination.is_file():
            if not temporary.is_file():
                raise MoveError("Verified copy is missing from both temporary and final paths.")
            self._verify_hash(temporary, operation.size, operation.content_hash)
            if transfer_destination.exists():
                raise MoveError("Final recovery path is occupied; no overwrite was attempted.")
            temporary.rename(transfer_destination)
        self._verify_hash(transfer_destination, operation.size, operation.content_hash)
        if temporary.is_file():
            self._verify_hash(temporary, operation.size, operation.content_hash)
            self._discard_temporary(temporary)

        if transfer_source.is_file():
            expected_modified = (
                operation.source_modified_ns
                if direction == "move"
                else operation.destination_modified_ns
            )
            if expected_modified is None:
                raise MoveError("Recovery record has no source modification snapshot.")
            self._validate_current_file(
                transfer_source,
                operation.size,
                expected_modified,
                "Transfer source changed after verification; it was not removed.",
            )
            self._verify_hash(transfer_source, operation.size, operation.content_hash)
            transfer_source.unlink()
            self._history.record_source_removed(operation.operation_id, direction=direction)
        elif not source_removed:
            self._history.record_source_removed(operation.operation_id, direction=direction)

        if direction == "move":
            destination_stat = transfer_destination.stat(follow_symlinks=False)
            self._history.record_move_completed(
                operation.operation_id, destination_stat.st_mtime_ns
            )
            message = "Verified cross-volume move recovered"
        else:
            self._history.record_undo_completed(operation.operation_id)
            message = "Verified cross-volume undo recovered"
        return MoveResult(
            operation.operation_id,
            transfer_source,
            transfer_destination,
            True,
            message,
        )

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

    def _ensure_free_space(self, directory: Path, required_bytes: int) -> None:
        try:
            available = self._volumes.free_bytes(directory)
        except OSError as error:
            raise MoveError(f"Cannot check free space for {directory}: {error}") from error
        if available < required_bytes:
            raise MoveError(
                f"Destination has insufficient free space: {available} available, "
                f"{required_bytes} required."
            )

    @staticmethod
    def _temporary_path(
        directory: Path, operation_id: str, direction: TransferDirection
    ) -> Path:
        return directory / f".jwdm-{operation_id}-{direction}.partial"

    @classmethod
    def _validate_temporary_path(
        cls,
        temporary: Path,
        destination: Path,
        operation_id: str,
        direction: TransferDirection,
    ) -> None:
        expected_name = cls._temporary_path(
            destination.parent, operation_id, direction
        ).name
        temporary_parent = os.path.normcase(os.path.abspath(temporary.parent))
        destination_parent = os.path.normcase(os.path.abspath(destination.parent))
        if temporary.name != expected_name or temporary_parent != destination_parent:
            raise MoveError(
                "Recovery record contains an unexpected temporary path; no file was removed."
            )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(MoveTransactionService._COPY_BUFFER_SIZE):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _verify_hash(cls, path: Path, expected_size: int, expected_hash: str) -> None:
        current = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or current.st_size != expected_size:
            raise MoveError(f"Recovery file does not match the expected size: {path}")
        if cls._sha256(path) != expected_hash:
            raise MoveError(f"Recovery SHA-256 mismatch; no source was removed: {path}")

    @staticmethod
    def _discard_temporary(path: Path) -> None:
        if not path.exists():
            return
        if _is_link_or_junction(path) or not path.is_file():
            raise MoveError(f"Refusing to remove unexpected temporary path: {path}")
        path.unlink()

    @staticmethod
    def _attempt_same_volume_rollback(source: Path, destination: Path) -> bool:
        if source.exists() or not destination.exists():
            return False
        try:
            destination.rename(source)
        except OSError:
            return False
        return True
