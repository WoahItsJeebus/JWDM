from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jwdm.persistence.history import HistoryError, HistoryOperation, HistoryRepository, OperationStatus
from jwdm.pipeline.models import ScanRoot
from jwdm.services.move_transaction import MoveError, MoveTransactionService
from jwdm.services.scan import ScanService


class _Volumes:
    def __init__(self, *, same_volume: bool, free_bytes: int = 1024**3) -> None:
        self._same_volume = same_volume
        self._free_bytes = free_bytes

    def same_volume(self, first: Path, second: Path) -> bool:
        return self._same_volume

    def identity(self, path: Path) -> str:
        return "volume-one" if "incoming" in path.parts else "volume-two"

    def free_bytes(self, path: Path) -> int:
        return self._free_bytes


def _ready_item(source: Path, library: Path):
    plan = ScanService().build_plan((ScanRoot(source.parent),), library)
    return next(item for item in plan.ready_items if item.source == source)


def test_move_is_journaled_and_undo_restores_exact_source(tmp_path: Path) -> None:
    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_bytes(b"important content")
    item = _ready_item(source, library)
    history = HistoryRepository(tmp_path / "history.jsonl")
    service = MoveTransactionService(history)

    result = service.execute(library, (item,))[0]

    assert result.succeeded
    assert not source.exists()
    assert result.destination is not None
    assert result.destination.read_bytes() == b"important content"
    operation = history.latest_undoable()
    assert operation is not None
    assert operation.status is OperationStatus.COMPLETED

    undo_result = service.undo(operation)

    assert undo_result.succeeded
    assert source.read_bytes() == b"important content"
    assert not operation.destination.exists()
    assert history.operations()[-1].status is OperationStatus.UNDONE


def test_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    documents = library / "Documents"
    source_dir.mkdir()
    documents.mkdir(parents=True)
    source = source_dir / "report.pdf"
    source.write_text("incoming", encoding="utf-8")
    existing = documents / "report.pdf"
    existing.write_text("existing", encoding="utf-8")
    item = _ready_item(source, library)
    service = MoveTransactionService(HistoryRepository(tmp_path / "history.jsonl"))

    result = service.execute(library, (item,))[0]

    assert result.succeeded
    assert existing.read_text(encoding="utf-8") == "existing"
    assert result.destination is not None
    assert result.destination.name == "report (1).pdf"
    assert result.destination.read_text(encoding="utf-8") == "incoming"


def test_changed_source_is_refused_before_intent_or_move(tmp_path: Path) -> None:
    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_text("first", encoding="utf-8")
    item = _ready_item(source, library)
    source.write_text("changed after preview", encoding="utf-8")
    os.utime(source, ns=(item.modified_ns + 1_000_000, item.modified_ns + 1_000_000))
    history = HistoryRepository(tmp_path / "history.jsonl")

    result = MoveTransactionService(history).execute(library, (item,))[0]

    assert not result.succeeded
    assert "changed after the preview" in result.message
    assert source.exists()
    assert history.operations() == ()


def test_undo_refuses_to_overwrite_reoccupied_original_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_text("moved", encoding="utf-8")
    history = HistoryRepository(tmp_path / "history.jsonl")
    service = MoveTransactionService(history)
    result = service.execute(library, (_ready_item(source, library),))[0]
    assert result.succeeded
    operation = history.latest_undoable()
    assert operation is not None
    source.write_text("new occupant", encoding="utf-8")

    with pytest.raises(MoveError, match="will not overwrite"):
        service.undo(operation)

    assert source.read_text(encoding="utf-8") == "new occupant"
    assert operation.destination.exists()


def test_history_failure_refuses_move_before_mutation(tmp_path: Path) -> None:
    class FailingHistory(HistoryRepository):
        def record_move_intended(self, operation: HistoryOperation) -> None:
            raise HistoryError("simulated unavailable journal")

    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_text("must remain", encoding="utf-8")
    item = _ready_item(source, library)

    result = MoveTransactionService(FailingHistory(tmp_path / "history.jsonl")).execute(
        library, (item,)
    )[0]

    assert not result.succeeded
    assert "unavailable journal" in result.message
    assert source.read_text(encoding="utf-8") == "must remain"
    assert not (library / "Documents" / "report.pdf").exists()


def test_cross_volume_move_is_hash_verified_and_undoable(tmp_path: Path) -> None:
    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_text("must remain", encoding="utf-8")
    item = _ready_item(source, library)
    history = HistoryRepository(tmp_path / "history.jsonl")

    service = MoveTransactionService(
        history,
        volume_service=_Volumes(same_volume=False),
    )
    result = service.execute(library, (item,))[0]

    assert result.succeeded
    assert not source.exists()
    assert result.destination is not None
    assert result.destination.read_text(encoding="utf-8") == "must remain"
    operation = history.latest_undoable()
    assert operation is not None
    assert operation.cross_volume
    assert operation.copy_verified
    assert operation.source_removed
    assert operation.content_hash == hashlib.sha256(b"must remain").hexdigest()

    undo = service.undo(operation)

    assert undo.succeeded
    assert source.read_text(encoding="utf-8") == "must remain"
    assert not operation.destination.exists()
    assert history.operations()[-1].status is OperationStatus.UNDONE


def test_insufficient_destination_space_refuses_before_journal_or_copy(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_bytes(b"important")
    history = HistoryRepository(tmp_path / "history.jsonl")
    service = MoveTransactionService(
        history,
        volume_service=_Volumes(same_volume=False, free_bytes=0),
    )

    result = service.execute(library, (_ready_item(source, library),))[0]

    assert not result.succeeded
    assert "insufficient free space" in result.message
    assert source.read_bytes() == b"important"
    assert history.operations() == ()


def test_recovery_finishes_only_a_durably_verified_cross_volume_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "report.pdf"
    destination = tmp_path / "library" / "Documents" / "report.pdf"
    source.parent.mkdir()
    destination.parent.mkdir(parents=True)
    payload = b"verified payload"
    source.write_bytes(payload)
    source_stat = source.stat()
    operation_id = "recovery-operation"
    temporary = destination.parent / f".jwdm-{operation_id}-move.partial"
    temporary.write_bytes(payload)
    history = HistoryRepository(tmp_path / "history.jsonl")
    operation = HistoryOperation(
        operation_id=operation_id,
        source=source,
        destination=destination,
        size=len(payload),
        source_modified_ns=source_stat.st_mtime_ns,
        category="Documents",
        reason="test",
        collision_behavior="none",
        status=OperationStatus.PENDING,
        planned_at=datetime.now(UTC),
        cross_volume=True,
        temporary_path=temporary,
    )
    history.record_move_intended(operation)
    history.record_copy_verified(
        operation_id,
        hashlib.sha256(payload).hexdigest(),
        direction="move",
    )

    results = MoveTransactionService(history).recover_pending()

    assert results[0].succeeded
    assert not source.exists()
    assert not temporary.exists()
    assert destination.read_bytes() == payload
    assert history.operations()[0].status is OperationStatus.COMPLETED


def test_recovery_discards_only_recorded_unverified_temporary_copy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "report.pdf"
    destination = tmp_path / "library" / "Documents" / "report.pdf"
    source.parent.mkdir()
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"source remains")
    source_stat = source.stat()
    operation_id = "partial-operation"
    temporary = destination.parent / f".jwdm-{operation_id}-move.partial"
    temporary.write_bytes(b"partial")
    history = HistoryRepository(tmp_path / "history.jsonl")
    history.record_move_intended(
        HistoryOperation(
            operation_id=operation_id,
            source=source,
            destination=destination,
            size=source_stat.st_size,
            source_modified_ns=source_stat.st_mtime_ns,
            category="Documents",
            reason="test",
            collision_behavior="none",
            status=OperationStatus.PENDING,
            planned_at=datetime.now(UTC),
            cross_volume=True,
            temporary_path=temporary,
        )
    )

    results = MoveTransactionService(history).recover_pending()

    assert not results[0].succeeded
    assert source.read_bytes() == b"source remains"
    assert not temporary.exists()
    assert not destination.exists()
    assert history.operations()[0].status is OperationStatus.FAILED


def test_recovery_never_deletes_source_when_verified_destination_hash_differs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "report.pdf"
    destination = tmp_path / "library" / "Documents" / "report.pdf"
    source.parent.mkdir()
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"original source")
    destination.write_bytes(b"different occupant")
    source_stat = source.stat()
    operation_id = "ambiguous-operation"
    history = HistoryRepository(tmp_path / "history.jsonl")
    history.record_move_intended(
        HistoryOperation(
            operation_id=operation_id,
            source=source,
            destination=destination,
            size=source_stat.st_size,
            source_modified_ns=source_stat.st_mtime_ns,
            category="Documents",
            reason="test",
            collision_behavior="none",
            status=OperationStatus.PENDING,
            planned_at=datetime.now(UTC),
            cross_volume=True,
            temporary_path=destination.parent
            / f".jwdm-{operation_id}-move.partial",
        )
    )
    history.record_copy_verified(
        operation_id,
        hashlib.sha256(b"original source").hexdigest(),
        direction="move",
    )

    results = MoveTransactionService(history).recover_pending()

    assert not results[0].succeeded
    assert source.read_bytes() == b"original source"
    assert destination.read_bytes() == b"different occupant"
    assert history.operations()[0].status is OperationStatus.RECOVERY_REQUIRED


def test_recovery_refuses_journal_temporary_path_outside_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "incoming" / "report.pdf"
    destination = tmp_path / "library" / "Documents" / "report.pdf"
    unrelated = tmp_path / "do-not-delete.txt"
    source.parent.mkdir()
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"source remains")
    unrelated.write_bytes(b"unrelated user data")
    source_stat = source.stat()
    history = HistoryRepository(tmp_path / "history.jsonl")
    history.record_move_intended(
        HistoryOperation(
            operation_id="edited-operation",
            source=source,
            destination=destination,
            size=source_stat.st_size,
            source_modified_ns=source_stat.st_mtime_ns,
            category="Documents",
            reason="test",
            collision_behavior="none",
            status=OperationStatus.PENDING,
            planned_at=datetime.now(UTC),
            cross_volume=True,
            temporary_path=unrelated,
        )
    )

    results = MoveTransactionService(history).recover_pending()

    assert not results[0].succeeded
    assert source.read_bytes() == b"source remains"
    assert unrelated.read_bytes() == b"unrelated user data"
    assert history.operations()[0].status is OperationStatus.RECOVERY_REQUIRED


def test_successful_move_registers_own_event_suppression(tmp_path: Path) -> None:
    class RecordingSuppressor:
        def __init__(self) -> None:
            self.paths: tuple[Path, Path] | None = None

        def suppress(self, source: Path, destination: Path) -> None:
            self.paths = (source, destination)

    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_text("move me", encoding="utf-8")
    suppressor = RecordingSuppressor()
    service = MoveTransactionService(
        HistoryRepository(tmp_path / "history.jsonl"), suppressor
    )

    result = service.execute(library, (_ready_item(source, library),))[0]

    assert result.succeeded and result.destination is not None
    assert suppressor.paths == (source, result.destination)
