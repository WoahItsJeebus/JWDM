"""Append-only move, undo, and recovery transaction history."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

HISTORY_SCHEMA_VERSION: Final = 1


class HistoryError(RuntimeError):
    """History could not be durably written or safely interpreted."""


class OperationStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    UNDO_PENDING = "undo_pending"
    UNDONE = "undone"
    UNDO_FAILED = "undo_failed"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class HistoryOperation:
    operation_id: str
    source: Path
    destination: Path
    size: int
    source_modified_ns: int
    category: str
    reason: str
    collision_behavior: str
    status: OperationStatus
    planned_at: datetime
    source_kind: str = "file"
    source_fingerprint: str | None = None
    cross_volume: bool = False
    temporary_path: Path | None = None
    source_volume_id: str | None = None
    destination_volume_id: str | None = None
    content_hash: str | None = None
    copy_verified: bool = False
    source_removed: bool = False
    undo_cross_volume: bool = False
    undo_temporary_path: Path | None = None
    undo_copy_verified: bool = False
    undo_source_removed: bool = False
    completed_at: datetime | None = None
    destination_modified_ns: int | None = None
    error: str | None = None


def default_history_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return base / "JWDM" / "history.jsonl"


class HistoryRepository:
    """Persist operation transitions before and after filesystem mutation."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else default_history_path()
        self._lock = threading.Lock()

    def record_move_intended(self, operation: HistoryOperation) -> None:
        self._append(
            {
                "record_type": "move_intended",
                "operation_id": operation.operation_id,
                "source": str(operation.source),
                "destination": str(operation.destination),
                "size": operation.size,
                "source_modified_ns": operation.source_modified_ns,
                "category": operation.category,
                "reason": operation.reason,
                "collision_behavior": operation.collision_behavior,
                "source_kind": operation.source_kind,
                "source_fingerprint": operation.source_fingerprint,
                "cross_volume": operation.cross_volume,
                "temporary_path": (
                    str(operation.temporary_path)
                    if operation.temporary_path is not None
                    else None
                ),
                "source_volume_id": operation.source_volume_id,
                "destination_volume_id": operation.destination_volume_id,
            }
        )

    def record_copy_verified(
        self, operation_id: str, content_hash: str, *, direction: str
    ) -> None:
        if direction not in {"move", "undo"}:
            raise HistoryError(f"Unsupported copy direction: {direction}")
        self._append(
            {
                "record_type": "copy_verified",
                "operation_id": operation_id,
                "direction": direction,
                "content_hash": content_hash,
            }
        )

    def record_source_removed(self, operation_id: str, *, direction: str) -> None:
        if direction not in {"move", "undo"}:
            raise HistoryError(f"Unsupported removal direction: {direction}")
        self._append(
            {
                "record_type": "source_removed",
                "operation_id": operation_id,
                "direction": direction,
            }
        )

    def record_move_completed(
        self, operation_id: str, destination_modified_ns: int
    ) -> None:
        self._append(
            {
                "record_type": "move_completed",
                "operation_id": operation_id,
                "destination_modified_ns": destination_modified_ns,
            }
        )

    def record_move_failed(self, operation_id: str, error: str) -> None:
        self._append(
            {"record_type": "move_failed", "operation_id": operation_id, "error": error}
        )

    def record_undo_intended(
        self,
        operation_id: str,
        *,
        cross_volume: bool = False,
        temporary_path: Path | None = None,
    ) -> None:
        self._append(
            {
                "record_type": "undo_intended",
                "operation_id": operation_id,
                "cross_volume": cross_volume,
                "temporary_path": str(temporary_path) if temporary_path else None,
            }
        )

    def record_undo_completed(self, operation_id: str) -> None:
        self._append({"record_type": "undo_completed", "operation_id": operation_id})

    def record_undo_failed(self, operation_id: str, error: str) -> None:
        self._append(
            {"record_type": "undo_failed", "operation_id": operation_id, "error": error}
        )

    def record_recovery_required(self, operation_id: str, error: str) -> None:
        self._append(
            {
                "record_type": "recovery_required",
                "operation_id": operation_id,
                "error": error,
            }
        )

    def operations(self) -> tuple[HistoryOperation, ...]:
        operations: dict[str, HistoryOperation] = {}
        order: list[str] = []
        for line_number, event in self._events():
            operation_id = self._required_text(event, "operation_id", line_number)
            record_type = self._required_text(event, "record_type", line_number)
            occurred_at = self._parse_timestamp(event, line_number)

            if record_type == "move_intended":
                if operation_id in operations:
                    raise HistoryError(
                        f"Duplicate move intention for {operation_id} on history line {line_number}."
                    )
                operation = HistoryOperation(
                    operation_id=operation_id,
                    source=Path(self._required_text(event, "source", line_number)),
                    destination=Path(self._required_text(event, "destination", line_number)),
                    size=self._required_int(event, "size", line_number),
                    source_modified_ns=self._required_int(
                        event, "source_modified_ns", line_number
                    ),
                    category=self._required_text(event, "category", line_number),
                    reason=self._required_text(event, "reason", line_number),
                    collision_behavior=self._required_text(
                        event, "collision_behavior", line_number
                    ),
                    status=OperationStatus.PENDING,
                    planned_at=occurred_at,
                    source_kind=self._optional_text(
                        event, "source_kind", line_number
                    )
                    or "file",
                    source_fingerprint=self._optional_text(
                        event, "source_fingerprint", line_number
                    ),
                    cross_volume=self._optional_bool(
                        event, "cross_volume", line_number, False
                    ),
                    temporary_path=self._optional_path(event, "temporary_path", line_number),
                    source_volume_id=self._optional_text(
                        event, "source_volume_id", line_number
                    ),
                    destination_volume_id=self._optional_text(
                        event, "destination_volume_id", line_number
                    ),
                )
                operations[operation_id] = operation
                order.append(operation_id)
                continue

            operation = operations.get(operation_id)
            if operation is None:
                raise HistoryError(
                    f"History line {line_number} references unknown operation {operation_id}."
                )
            if record_type == "move_completed":
                operations[operation_id] = replace(
                    operation,
                    status=OperationStatus.COMPLETED,
                    completed_at=occurred_at,
                    destination_modified_ns=self._required_int(
                        event, "destination_modified_ns", line_number
                    ),
                    error=None,
                )
            elif record_type == "move_failed":
                operations[operation_id] = replace(
                    operation,
                    status=OperationStatus.FAILED,
                    error=self._required_text(event, "error", line_number),
                )
            elif record_type == "undo_intended":
                operations[operation_id] = replace(
                    operation,
                    status=OperationStatus.UNDO_PENDING,
                    undo_cross_volume=self._optional_bool(
                        event, "cross_volume", line_number, False
                    ),
                    undo_temporary_path=self._optional_path(
                        event, "temporary_path", line_number
                    ),
                    error=None,
                )
            elif record_type == "copy_verified":
                direction = self._required_text(event, "direction", line_number)
                content_hash = self._required_text(event, "content_hash", line_number)
                if direction == "move":
                    operations[operation_id] = replace(
                        operation,
                        content_hash=content_hash,
                        copy_verified=True,
                    )
                elif direction == "undo":
                    operations[operation_id] = replace(
                        operation,
                        content_hash=content_hash,
                        undo_copy_verified=True,
                    )
                else:
                    raise HistoryError(
                        f"Invalid copy direction {direction!r} on line {line_number}."
                    )
            elif record_type == "source_removed":
                direction = self._required_text(event, "direction", line_number)
                if direction == "move":
                    operations[operation_id] = replace(operation, source_removed=True)
                elif direction == "undo":
                    operations[operation_id] = replace(operation, undo_source_removed=True)
                else:
                    raise HistoryError(
                        f"Invalid removal direction {direction!r} on line {line_number}."
                    )
            elif record_type == "undo_completed":
                operations[operation_id] = replace(
                    operation, status=OperationStatus.UNDONE, error=None
                )
            elif record_type == "undo_failed":
                operations[operation_id] = replace(
                    operation,
                    status=OperationStatus.UNDO_FAILED,
                    error=self._required_text(event, "error", line_number),
                )
            elif record_type == "recovery_required":
                operations[operation_id] = replace(
                    operation,
                    status=OperationStatus.RECOVERY_REQUIRED,
                    error=self._required_text(event, "error", line_number),
                )
            else:
                raise HistoryError(
                    f"Unknown history record type {record_type!r} on line {line_number}."
                )

        return tuple(operations[operation_id] for operation_id in order)

    def latest_undoable(self) -> HistoryOperation | None:
        for operation in reversed(self.operations()):
            if operation.status in {OperationStatus.COMPLETED, OperationStatus.UNDO_FAILED}:
                return operation
        return None

    def pending_operations(self) -> tuple[HistoryOperation, ...]:
        return tuple(
            operation
            for operation in self.operations()
            if operation.status in {OperationStatus.PENDING, OperationStatus.UNDO_PENDING}
        )

    def operation(self, operation_id: str) -> HistoryOperation:
        for operation in self.operations():
            if operation.operation_id == operation_id:
                return operation
        raise HistoryError(f"Unknown operation: {operation_id}")

    def _append(self, event: dict[str, Any]) -> None:
        payload = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            **event,
        }
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as error:
            raise HistoryError(f"Cannot write operation history at {self.path}: {error}") from error

    def _events(self) -> tuple[tuple[int, dict[str, Any]], ...]:
        if not self.path.exists():
            return ()
        parsed: list[tuple[int, dict[str, Any]]] = []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise HistoryError(
                            f"Invalid JSON in operation history line {line_number}: {error}"
                        ) from error
                    if not isinstance(event, dict):
                        raise HistoryError(
                            f"Operation history line {line_number} is not a JSON object."
                        )
                    if event.get("schema_version") != HISTORY_SCHEMA_VERSION:
                        raise HistoryError(
                            f"Unsupported operation history schema on line {line_number}."
                        )
                    parsed.append((line_number, event))
        except OSError as error:
            raise HistoryError(f"Cannot read operation history at {self.path}: {error}") from error
        return tuple(parsed)

    @staticmethod
    def _required_text(event: dict[str, Any], key: str, line_number: int) -> str:
        value = event.get(key)
        if not isinstance(value, str) or not value:
            raise HistoryError(f"Missing text field {key!r} on history line {line_number}.")
        return value

    @staticmethod
    def _required_int(event: dict[str, Any], key: str, line_number: int) -> int:
        value = event.get(key)
        if not isinstance(value, int):
            raise HistoryError(f"Missing integer field {key!r} on history line {line_number}.")
        return value

    @staticmethod
    def _optional_text(
        event: dict[str, Any], key: str, line_number: int
    ) -> str | None:
        value = event.get(key)
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise HistoryError(f"Invalid optional text field {key!r} on line {line_number}.")
        return value

    @staticmethod
    def _optional_path(
        event: dict[str, Any], key: str, line_number: int
    ) -> Path | None:
        value = HistoryRepository._optional_text(event, key, line_number)
        return Path(value) if value is not None else None

    @staticmethod
    def _optional_bool(
        event: dict[str, Any], key: str, line_number: int, default: bool
    ) -> bool:
        value = event.get(key, default)
        if not isinstance(value, bool):
            raise HistoryError(f"Invalid boolean field {key!r} on line {line_number}.")
        return value

    @staticmethod
    def _parse_timestamp(event: dict[str, Any], line_number: int) -> datetime:
        value = HistoryRepository._required_text(event, "timestamp", line_number)
        try:
            return datetime.fromisoformat(value)
        except ValueError as error:
            raise HistoryError(
                f"Invalid timestamp on operation history line {line_number}: {value}"
            ) from error
