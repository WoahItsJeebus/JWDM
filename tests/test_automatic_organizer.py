from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jwdm.classification.rule_classifier import RuleClassifier
from jwdm.config import ConfidencePolicy, ExtensionRule, RuleAction
from jwdm.persistence.history import HistoryRepository
from jwdm.persistence.state import StateRepository
from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState
from jwdm.pipeline.result import StageOutcome, StageResult
from jwdm.pipeline.stages.access import AccessProbe
from jwdm.pipeline.stages.stability import ReadinessConfig
from jwdm.services.automatic_organizer import AutomaticOrganizer
from jwdm.services.move_transaction import MoveTransactionService
from jwdm.services.operation_suppression import OperationSuppressor
from jwdm.watcher.events import FileWatchEvent, WatchEventType


class _AlwaysAvailable:
    def probe(self, path: Path) -> StageResult:
        return StageResult(StageOutcome.PASS, "available")


class _BlockedThenAvailable:
    def __init__(self) -> None:
        self.calls = 0

    def probe(self, path: Path) -> StageResult:
        self.calls += 1
        if self.calls == 1:
            return StageResult(StageOutcome.DEFER, "simulated sharing violation", 32)
        return StageResult(StageOutcome.PASS, "available")


class _FakeWatcher:
    def __init__(self) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


class _WatcherFactory:
    def __init__(self) -> None:
        self.watcher = _FakeWatcher()

    def __call__(self, root: Path, callback, suppressor) -> _FakeWatcher:
        return self.watcher


class _RecordingWatcherFactory:
    def __init__(self) -> None:
        self.roots: list[Path] = []
        self.watchers: list[_FakeWatcher] = []

    def __call__(self, root: Path, callback, suppressor) -> _FakeWatcher:
        watcher = _FakeWatcher()
        self.roots.append(root)
        self.watchers.append(watcher)
        return watcher


def _service(
    tmp_path: Path,
    access_probe: AccessProbe | None = None,
    *,
    confidence_policy=None,
) -> tuple[AutomaticOrganizer, Path, Path, HistoryRepository]:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    history = HistoryRepository(tmp_path / "history.jsonl")
    suppressor = OperationSuppressor()
    config = ReadinessConfig(
        sample_interval_seconds=3600,
        required_stable_samples=2,
        minimum_quiet_seconds=1,
        retry_base_seconds=1,
        retry_max_seconds=4,
    )
    organizer = AutomaticOrganizer(
        MoveTransactionService(history, suppressor),
        suppressor,
        access_probe=access_probe or _AlwaysAvailable(),
        config=config,
        watcher_factory=_WatcherFactory(),
        confidence_policy=confidence_policy,
    )
    organizer.start(incoming, library)
    return organizer, incoming, library, history


def test_stable_known_file_is_moved_once_and_recorded(tmp_path: Path) -> None:
    organizer, incoming, library, history = _service(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    source = incoming / "report.pdf"
    source.write_bytes(b"report")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    organizer.handle_event(FileWatchEvent(WatchEventType.MODIFIED, source), started)
    try:
        assert len(organizer.snapshots()) == 1
        organizer.tick(started)
        assert source.exists()
        organizer.tick(started + timedelta(seconds=1))

        assert not source.exists()
        assert (library / "Documents" / "report.pdf").read_bytes() == b"report"
        assert organizer.snapshots()[0].state is CandidateState.MOVED
        assert history.latest_undoable() is not None
    finally:
        organizer.stop()


def test_changing_file_waits_then_moves_after_new_quiet_window(tmp_path: Path) -> None:
    organizer, incoming, library, _ = _service(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    source = incoming / "growing.pdf"
    source.write_bytes(b"one")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    try:
        organizer.tick(started)
        source.write_bytes(b"one plus more")
        os.utime(source, ns=(2_000_000_000, 2_000_000_000))
        organizer.handle_event(
            FileWatchEvent(WatchEventType.MODIFIED, source),
            started + timedelta(milliseconds=500),
        )
        organizer.tick(started + timedelta(milliseconds=500))
        organizer.tick(started + timedelta(seconds=1))
        assert source.exists()

        organizer.tick(started + timedelta(seconds=1.5))
        assert not source.exists()
        assert (library / "Documents" / "growing.pdf").exists()
    finally:
        organizer.stop()


def test_partial_rename_keeps_identity_and_runs_full_readiness(tmp_path: Path) -> None:
    organizer, incoming, library, _ = _service(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    partial = incoming / "download.crdownload"
    final = incoming / "download.pdf"
    partial.write_bytes(b"complete")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, partial), started)
    try:
        organizer.tick(started)
        original_id = organizer.snapshots()[0].candidate_id
        assert organizer.snapshots()[0].state is CandidateState.DOWNLOADING
        partial.rename(final)
        organizer.handle_event(
            FileWatchEvent(WatchEventType.MOVED, partial, final),
            started + timedelta(seconds=1),
        )
        assert organizer.snapshots()[0].candidate_id == original_id
        organizer.tick(started + timedelta(seconds=1))
        organizer.tick(started + timedelta(seconds=2))

        assert not final.exists()
        assert (library / "Documents" / "download.pdf").exists()
    finally:
        organizer.stop()


def test_unknown_file_needs_review_and_pause_stops_processing(tmp_path: Path) -> None:
    organizer, incoming, _, _ = _service(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    source = incoming / "unknown.format"
    source.write_bytes(b"unknown")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    organizer.pause()
    try:
        organizer.tick(started + timedelta(seconds=5))
        assert organizer.snapshots()[0].state is CandidateState.DETECTED
        organizer.resume()
        organizer.tick(started)
        organizer.tick(started + timedelta(seconds=1))

        assert source.exists()
        assert organizer.snapshots()[0].state is CandidateState.NEEDS_REVIEW
    finally:
        organizer.stop()


def test_new_rule_requeues_and_moves_all_matching_review_candidates(
    tmp_path: Path,
) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    state = StateRepository(tmp_path / "state.db")
    history = HistoryRepository(tmp_path / "history.jsonl")
    suppressor = OperationSuppressor()
    organizer = AutomaticOrganizer(
        MoveTransactionService(history, suppressor),
        suppressor,
        classifier=RuleClassifier(state),
        access_probe=_AlwaysAvailable(),
        config=ReadinessConfig(
            sample_interval_seconds=3600,
            required_stable_samples=2,
            minimum_quiet_seconds=1,
        ),
        watcher_factory=_WatcherFactory(),
    )
    organizer.start(incoming, library)
    published: list[tuple[CandidateSnapshot, ...]] = []
    organizer.subscribe(published.append)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    first = incoming / "first.ahk"
    second = incoming / "second.AHK"
    unrelated = incoming / "other.widget"
    for source in (first, second, unrelated):
        source.write_bytes(b"reviewed")
        organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    try:
        organizer.tick(started)
        organizer.tick(started + timedelta(seconds=1))
        assert all(
            candidate.state is CandidateState.NEEDS_REVIEW
            for candidate in organizer.snapshots()
        )

        state.upsert_rules(
            (
                ExtensionRule(
                    ".ahk",
                    RuleAction.ROUTE,
                    "Code/AutoHotkey",
                ),
            )
        )
        retried = organizer.retry_reviews_for_extensions(
            (".ahk",),
            occurred_at=started + timedelta(seconds=2),
        )

        restarted = {
            candidate.source_path.name: candidate
            for candidate in organizer.snapshots()
        }
        assert retried == 2
        assert restarted["first.ahk"].state is CandidateState.DETECTED
        assert restarted["second.AHK"].state is CandidateState.DETECTED
        assert restarted["first.ahk"].stable_samples == 0
        assert restarted["other.widget"].state is CandidateState.NEEDS_REVIEW
        assert published
        assert sum(
            candidate.state is CandidateState.DETECTED
            for candidate in published[-1]
        ) == 2

        organizer.tick(started + timedelta(seconds=2))
        organizer.tick(started + timedelta(seconds=3))

        assert not first.exists()
        assert not second.exists()
        assert (library / "Code" / "AutoHotkey" / "first.ahk").exists()
        assert (library / "Code" / "AutoHotkey" / "second.AHK").exists()
        remaining = {
            candidate.source_path.name: candidate
            for candidate in organizer.snapshots()
        }
        assert remaining["first.ahk"].state is CandidateState.MOVED
        assert remaining["second.AHK"].state is CandidateState.MOVED
        assert remaining["other.widget"].state is CandidateState.NEEDS_REVIEW
    finally:
        organizer.stop()


def test_locked_file_defers_then_moves_after_access_returns(tmp_path: Path) -> None:
    probe = _BlockedThenAvailable()
    organizer, incoming, library, _ = _service(tmp_path, probe)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    source = incoming / "locked.pdf"
    source.write_bytes(b"locked then released")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    try:
        organizer.tick(started)
        organizer.tick(started + timedelta(seconds=1))
        assert source.exists()
        assert organizer.snapshots()[0].state is CandidateState.WAITING_FOR_ACCESS

        organizer.tick(started + timedelta(seconds=2))
        assert not source.exists()
        assert (library / "Documents" / "locked.pdf").exists()
        assert probe.calls == 2
    finally:
        organizer.stop()


def test_disappearing_library_queues_and_reconnect_resumes_without_fallback(
    tmp_path: Path,
) -> None:
    organizer, incoming, library, _ = _service(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    source = incoming / "report.pdf"
    source.write_bytes(b"safe source")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    try:
        organizer.tick(started)
        library.rmdir()
        organizer.tick(started + timedelta(seconds=1))

        assert source.read_bytes() == b"safe source"
        assert organizer.snapshots()[0].state is CandidateState.QUEUED_FOR_DESTINATION
        assert "Library unavailable" in organizer.snapshots()[0].detail

        library.mkdir()
        organizer.tick(started + timedelta(seconds=2))
        assert not source.exists()
        assert (library / "Documents" / "report.pdf").exists()
    finally:
        organizer.stop()


def test_review_all_policy_never_moves_recognized_file(tmp_path: Path) -> None:
    organizer, incoming, _, _ = _service(
        tmp_path,
        confidence_policy=lambda: ConfidencePolicy.REVIEW_ALL,
    )
    started = datetime(2026, 1, 1, tzinfo=UTC)
    source = incoming / "report.pdf"
    source.write_bytes(b"safe source")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, source), started)
    try:
        organizer.tick(started)
        organizer.tick(started + timedelta(seconds=1))

        assert source.exists()
        assert organizer.snapshots()[0].state is CandidateState.NEEDS_REVIEW
        assert "Confidence policy requires review" in organizer.snapshots()[0].detail
    finally:
        organizer.stop()


def test_pending_candidate_restores_and_existing_scan_is_opt_in(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming"
    library = tmp_path / "library"
    incoming.mkdir()
    library.mkdir()
    state = StateRepository(tmp_path / "state.db")
    history = HistoryRepository(tmp_path / "history.jsonl")
    suppressor = OperationSuppressor()
    config = ReadinessConfig(
        sample_interval_seconds=3600,
        required_stable_samples=2,
        minimum_quiet_seconds=1,
    )
    source = incoming / "pending.pdf"
    source.write_bytes(b"pending")

    first = AutomaticOrganizer(
        MoveTransactionService(history, suppressor),
        suppressor,
        access_probe=_AlwaysAvailable(),
        config=config,
        watcher_factory=_WatcherFactory(),
        state_repository=state,
    )
    first.start(incoming, library)
    first.handle_event(
        FileWatchEvent(WatchEventType.CREATED, source),
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    first.stop()

    restored = AutomaticOrganizer(
        MoveTransactionService(history, suppressor),
        suppressor,
        access_probe=_AlwaysAvailable(),
        config=config,
        watcher_factory=_WatcherFactory(),
        state_repository=state,
    )
    restored.start(incoming, library)
    try:
        assert [candidate.source_path for candidate in restored.snapshots()] == [source]
        assert restored.snapshots()[0].stable_samples == 0
    finally:
        restored.stop()

    state.save_candidates(incoming, ())
    catch_up = AutomaticOrganizer(
        MoveTransactionService(history, suppressor),
        suppressor,
        access_probe=_AlwaysAvailable(),
        config=config,
        watcher_factory=_WatcherFactory(),
        state_repository=state,
    )
    catch_up.start(incoming, library, process_existing=True)
    try:
        assert [candidate.source_path for candidate in catch_up.snapshots()] == [source]
    finally:
        catch_up.stop()


def test_multiple_incoming_folders_are_watched_and_processed(tmp_path: Path) -> None:
    first = tmp_path / "first-incoming"
    second = tmp_path / "second-incoming"
    library = tmp_path / "library"
    for path in (first, second, library):
        path.mkdir()
    history = HistoryRepository(tmp_path / "history.jsonl")
    suppressor = OperationSuppressor()
    factory = _RecordingWatcherFactory()
    organizer = AutomaticOrganizer(
        MoveTransactionService(history, suppressor),
        suppressor,
        access_probe=_AlwaysAvailable(),
        config=ReadinessConfig(
            sample_interval_seconds=3600,
            required_stable_samples=2,
            minimum_quiet_seconds=1,
        ),
        watcher_factory=factory,
    )
    organizer.start((first, second), library)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    one = first / "one.pdf"
    two = second / "two.mp3"
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, one), started)
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, two), started)
    try:
        organizer.tick(started)
        organizer.tick(started + timedelta(seconds=1))

        assert factory.roots == [first.resolve(), second.resolve()]
        assert (library / "Documents" / "one.pdf").exists()
        assert (library / "Audio" / "two.mp3").exists()
    finally:
        organizer.stop()


def test_stable_top_level_folder_moves_as_one_undoable_candidate(tmp_path: Path) -> None:
    organizer, incoming, library, history = _service(tmp_path)
    started = datetime(2026, 1, 1, tzinfo=UTC)
    folder = incoming / "Downloaded Project"
    folder.mkdir()
    (folder / "asset.bin").write_bytes(b"asset")
    organizer.handle_event(FileWatchEvent(WatchEventType.CREATED, folder), started)
    try:
        organizer.tick(started)
        organizer.tick(started + timedelta(seconds=1))

        moved = library / "Folders" / "Downloaded Project"
        assert not folder.exists()
        assert (moved / "asset.bin").read_bytes() == b"asset"
        assert organizer.snapshots()[0].state is CandidateState.MOVED
        assert history.latest_undoable() is not None
    finally:
        organizer.stop()
