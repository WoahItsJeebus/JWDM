from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from jwdm.pipeline.candidate import CandidateState
from jwdm.services.candidate_registry import CandidateRegistry


def test_repeated_events_deduplicate_one_active_candidate(tmp_path: Path) -> None:
    registry = CandidateRegistry()
    root = tmp_path
    path = root / "download.pdf"
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = registry.register_event(path, root, "created", now)
    second = registry.register_event(path, root, "modified", now + timedelta(milliseconds=10))

    assert first.candidate_id == second.candidate_id
    assert len(registry.snapshots()) == 1
    assert second.signals == ("created", "modified")


def test_rename_preserves_identity_and_resets_stability(tmp_path: Path) -> None:
    registry = CandidateRegistry()
    root = tmp_path
    partial = root / "file.crdownload"
    final = root / "file.pdf"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = registry.register_event(partial, root, "created", now)
    registry.observe(candidate.candidate_id, 10, 100, now)

    renamed = registry.rename(partial, final, root, now + timedelta(seconds=1))

    assert renamed.candidate_id == candidate.candidate_id
    assert renamed.source_path == final
    assert renamed.stable_samples == 0
    assert renamed.last_size is None
    assert renamed.state is CandidateState.DETECTED


def test_changed_snapshot_restarts_stable_samples(tmp_path: Path) -> None:
    registry = CandidateRegistry()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = registry.register_event(tmp_path / "file.pdf", tmp_path, "created", now)

    first, first_changed = registry.observe(candidate.candidate_id, 10, 100, now)
    second, second_changed = registry.observe(
        candidate.candidate_id, 10, 100, now + timedelta(seconds=1)
    )
    third, third_changed = registry.observe(
        candidate.candidate_id, 11, 101, now + timedelta(seconds=2)
    )

    assert first is not None and first.stable_samples == 1 and not first_changed
    assert second is not None and second.stable_samples == 2 and not second_changed
    assert third is not None and third.stable_samples == 1 and third_changed

