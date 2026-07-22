from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from jwdm.config import DownloadsRelocationRecord, DownloadsRelocationState
from jwdm.persistence.state import StateRepository
from jwdm.services.downloads import (
    DownloadsRelocationError,
    DownloadsRelocationService,
)


class _KnownFolders:
    def __init__(self, current: Path) -> None:
        self.current = current
        self.redirects: list[Path] = []
        self.fail_before_change = False
        self.fail_after_change = False

    def current_downloads(self) -> Path:
        return self.current

    def redirect_downloads(self, target: Path) -> None:
        self.redirects.append(target)
        if self.fail_before_change:
            raise OSError("simulated shell refusal")
        self.current = target
        if self.fail_after_change:
            raise OSError("simulated ambiguous shell result")


def test_relocation_records_restore_point_and_never_moves_existing_files(
    tmp_path: Path,
) -> None:
    original = tmp_path / "Downloads"
    target = tmp_path / "JWDM" / "Incoming"
    library = tmp_path / "JWDM" / "Library"
    original.mkdir()
    target.mkdir(parents=True)
    library.mkdir()
    original_file = original / "existing.pdf"
    target_file = target / "already-there.txt"
    original_file.write_text("original", encoding="utf-8")
    target_file.write_text("target", encoding="utf-8")
    repository = StateRepository(tmp_path / "state.db")
    backend = _KnownFolders(original)
    service = DownloadsRelocationService(repository, backend)

    relocated = service.relocate(target, library_path=library)

    assert relocated.current_path == target
    assert relocated.can_restore
    assert original_file.read_text(encoding="utf-8") == "original"
    assert target_file.read_text(encoding="utf-8") == "target"
    record = repository.downloads_relocation()
    assert record is not None
    assert record.original_path == original
    assert record.relocated_path == target
    assert record.state is DownloadsRelocationState.ACTIVE

    restored = service.restore()

    assert restored.current_path == original
    assert not restored.can_restore
    assert repository.downloads_relocation().state is DownloadsRelocationState.RESTORED  # type: ignore[union-attr]
    assert original_file.exists()
    assert target_file.exists()


def test_relocation_refuses_overlaps_and_drive_roots(tmp_path: Path) -> None:
    original = tmp_path / "Downloads"
    target = original / "Nested"
    original.mkdir()
    target.mkdir()
    service = DownloadsRelocationService(
        StateRepository(tmp_path / "state.db"), _KnownFolders(original)
    )

    with pytest.raises(DownloadsRelocationError, match="contain"):
        service.relocate(target)
    with pytest.raises(DownloadsRelocationError, match="drive root"):
        service.relocate(Path(tmp_path.anchor))
    with pytest.raises(DownloadsRelocationError, match="network path"):
        service.relocate(Path(r"\\server\share\Downloads"))


def test_relocation_refuses_library_overlap(tmp_path: Path) -> None:
    original = tmp_path / "Downloads"
    library = tmp_path / "Library"
    target = library / "Incoming"
    original.mkdir()
    target.mkdir(parents=True)
    service = DownloadsRelocationService(
        StateRepository(tmp_path / "state.db"), _KnownFolders(original)
    )

    with pytest.raises(DownloadsRelocationError, match="library cannot overlap"):
        service.relocate(target, library_path=library)


def test_shell_failure_keeps_a_durable_observed_state(tmp_path: Path) -> None:
    original = tmp_path / "Downloads"
    target = tmp_path / "Incoming"
    original.mkdir()
    target.mkdir()
    repository = StateRepository(tmp_path / "state.db")
    backend = _KnownFolders(original)
    backend.fail_after_change = True
    service = DownloadsRelocationService(repository, backend)

    with pytest.raises(DownloadsRelocationError, match="not safely relocated"):
        service.relocate(target)

    record = repository.downloads_relocation()
    assert record is not None
    assert record.state is DownloadsRelocationState.ACTIVE
    assert record.error is not None
    assert service.status().can_restore


def test_prepared_checkpoint_is_reconciled_after_restart(tmp_path: Path) -> None:
    original = tmp_path / "Downloads"
    target = tmp_path / "Incoming"
    original.mkdir()
    target.mkdir()
    repository = StateRepository(tmp_path / "state.db")
    timestamp = datetime.now(UTC)
    repository.save_downloads_relocation(
        DownloadsRelocationRecord(
            original,
            target,
            DownloadsRelocationState.PREPARED,
            timestamp,
            timestamp,
        )
    )

    status = DownloadsRelocationService(repository, _KnownFolders(target)).status()

    assert status.can_restore
    assert status.record is not None
    assert status.record.state is DownloadsRelocationState.ACTIVE


def test_external_path_change_requires_manual_recovery(tmp_path: Path) -> None:
    original = tmp_path / "Downloads"
    target = tmp_path / "Incoming"
    other = tmp_path / "Elsewhere"
    original.mkdir()
    target.mkdir()
    other.mkdir()
    repository = StateRepository(tmp_path / "state.db")
    backend = _KnownFolders(original)
    service = DownloadsRelocationService(repository, backend)
    service.relocate(target)
    backend.current = other

    status = service.status()

    assert status.record is not None
    assert status.record.state is DownloadsRelocationState.RECOVERY_REQUIRED
    assert not status.can_restore
    with pytest.raises(DownloadsRelocationError, match="does not match"):
        service.restore()
