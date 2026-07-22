from __future__ import annotations

import os
from pathlib import Path

import pytest

from jwdm.persistence.history import HistoryError, HistoryOperation, HistoryRepository, OperationStatus
from jwdm.pipeline.models import ScanRoot
from jwdm.services.move_transaction import MoveError, MoveTransactionService
from jwdm.services.scan import ScanService


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


def test_cross_volume_move_is_deferred_without_mutation(tmp_path: Path) -> None:
    class CrossVolumeService(MoveTransactionService):
        @staticmethod
        def _same_volume(path: Path, directory: Path) -> bool:
            return False

    source_dir = tmp_path / "incoming"
    library = tmp_path / "library"
    source_dir.mkdir()
    library.mkdir()
    source = source_dir / "report.pdf"
    source.write_text("must remain", encoding="utf-8")
    item = _ready_item(source, library)
    history = HistoryRepository(tmp_path / "history.jsonl")

    result = CrossVolumeService(history).execute(library, (item,))[0]

    assert not result.succeeded
    assert "deferred until Phase 4" in result.message
    assert source.read_text(encoding="utf-8") == "must remain"
    assert history.operations() == ()


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
