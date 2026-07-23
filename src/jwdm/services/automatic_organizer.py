"""Multi-root automatic event, readiness, classification, and move orchestration."""

from __future__ import annotations

import logging
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from jwdm.classification.rule_classifier import Classifier
from jwdm.classification.smart_classifier import SmartClassifier
from jwdm.config import ConfidencePolicy
from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.state import StateError, StateRepository
from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState
from jwdm.pipeline.models import (
    Classification,
    ClassificationDisposition,
    PlanItem,
    PlanItemKind,
    PlanItemStatus,
)
from jwdm.pipeline.result import StageOutcome
from jwdm.pipeline.stages.access import AccessProbe, WindowsAccessProbe
from jwdm.pipeline.stages.stability import ReadinessConfig, StabilityStage
from jwdm.pipeline.stages.temporary import TemporaryFileStage
from jwdm.services.candidate_registry import CandidateRegistry
from jwdm.services.destinations import destination_for
from jwdm.services.exclusions import ExclusionMatcher
from jwdm.services.folder_snapshot import FolderSnapshotError, snapshot_folder
from jwdm.services.move_transaction import MoveTransactionService
from jwdm.services.operation_suppression import OperationSuppressor
from jwdm.services.path_validation import PathValidator
from jwdm.services.volumes import DestinationStatus, VolumeService
from jwdm.watcher.directory_watcher import DirectoryWatcher, WatcherError
from jwdm.watcher.events import FileWatchEvent, WatchEventType


class Watcher(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


WatcherFactory = Callable[
    [Path, Callable[[FileWatchEvent], None], OperationSuppressor], Watcher
]
CandidateCallback = Callable[[tuple[CandidateSnapshot, ...]], None]
DestinationCallback = Callable[[DestinationStatus], None]


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


class AutomaticOrganizer:
    """Run one safe automatic pipeline across configured incoming folders."""

    def __init__(
        self,
        moves: MoveTransactionService,
        suppressor: OperationSuppressor,
        *,
        registry: CandidateRegistry | None = None,
        classifier: Classifier | None = None,
        access_probe: AccessProbe | None = None,
        config: ReadinessConfig | None = None,
        watcher_factory: WatcherFactory | None = None,
        exclusions: ExclusionMatcher | None = None,
        state_repository: StateRepository | None = None,
        confidence_policy: Callable[[], ConfidencePolicy] | None = None,
        destination_resolver: Callable[[Path], DestinationStatus] | None = None,
    ) -> None:
        self._moves = moves
        self._suppressor = suppressor
        self._registry = registry or CandidateRegistry()
        self._classifier = classifier or SmartClassifier()
        self._access_probe = access_probe or WindowsAccessProbe()
        self._config = config or ReadinessConfig()
        self._stability = StabilityStage(self._config)
        self._temporary = TemporaryFileStage()
        self._watcher_factory = watcher_factory or DirectoryWatcher
        self._exclusions = exclusions
        self._state_repository = state_repository
        self._confidence_policy = confidence_policy or (
            lambda: ConfidencePolicy.MOVE_RECOGNIZED
        )
        default_volumes = VolumeService()
        self._destination_resolver = destination_resolver or (
            lambda path: default_volumes.resolve(path, None)
        )
        self._validator = PathValidator()
        self._callbacks: list[CandidateCallback] = []
        self._destination_callbacks: list[DestinationCallback] = []
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._watchers: tuple[Watcher, ...] = ()
        self._incoming_root: Path | None = None
        self._incoming_roots: tuple[Path, ...] = ()
        self._library_root: Path | None = None
        self._destination_status: DestinationStatus | None = None
        self._running = False
        self._paused = False
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.automatic")

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    @property
    def incoming_root(self) -> Path | None:
        with self._state_lock:
            return self._incoming_root

    @property
    def incoming_roots(self) -> tuple[Path, ...]:
        with self._state_lock:
            return self._incoming_roots

    def subscribe(self, callback: CandidateCallback) -> None:
        self._callbacks.append(callback)

    def subscribe_destination(self, callback: DestinationCallback) -> None:
        self._destination_callbacks.append(callback)

    def snapshots(self) -> tuple[CandidateSnapshot, ...]:
        return self._registry.snapshots()

    def snapshot(self, candidate_id: str) -> CandidateSnapshot | None:
        return self._registry.get(candidate_id)

    def retry_reviews_for_extensions(
        self,
        extensions: tuple[str, ...],
        occurred_at: datetime | None = None,
    ) -> int:
        """Requeue every reviewed file matched by a changed extension rule."""

        normalized = tuple(
            sorted(
                {
                    extension.casefold()
                    for extension in extensions
                    if extension.startswith(".") and len(extension) > 1
                }
            )
        )
        if not normalized:
            return 0
        timestamp = occurred_at or datetime.now(UTC)
        retried: list[CandidateSnapshot] = []
        for candidate in self._registry.snapshots():
            if (
                candidate.state is CandidateState.NEEDS_REVIEW
                and candidate.source_path.name.casefold().endswith(normalized)
            ):
                reset = self._registry.reset_for_review_retry(
                    candidate.candidate_id, timestamp
                )
                if reset is not None:
                    retried.append(reset)
        if retried:
            self._logger.info(
                "Candidate reviews restarted after rule change",
                extra={
                    "event": "candidate_reviews_retried",
                    "count": len(retried),
                    "extensions": ";".join(normalized),
                },
            )
            self._publish()
        return len(retried)

    def start(
        self,
        incoming_root: Path | tuple[Path, ...],
        library_root: Path,
        *,
        process_existing: bool = False,
    ) -> None:
        with self._state_lock:
            if self._running:
                raise RuntimeError("Automatic organization is already running.")
        destination_status = self._destination_resolver(library_root)
        requested_roots = (
            (incoming_root,) if isinstance(incoming_root, Path) else tuple(incoming_root)
        )
        validated = self._validator.validate_automatic_roots(
            requested_roots,
            library_root,
            destination_status.path if destination_status.available else None,
        )
        normalized_incoming = tuple(root.path for root in validated.roots)
        configured_library = library_root.expanduser().resolve(strict=False)

        watchers = tuple(
            self._watcher_factory(
                root,
                lambda event, watched_root=root: self.handle_event(
                    event, incoming_root=watched_root
                ),
                self._suppressor,
            )
            for root in normalized_incoming
        )
        self._registry.clear()
        self._stop_event.clear()
        worker = threading.Thread(
            target=self._run,
            name="JWDM automatic readiness",
            daemon=True,
        )
        with self._state_lock:
            self._incoming_root = normalized_incoming[0]
            self._incoming_roots = normalized_incoming
            self._library_root = configured_library
            self._destination_status = destination_status
            self._watchers = watchers
            self._worker = worker
            self._paused = False
            self._running = True
        started_watchers: list[Watcher] = []
        try:
            for watcher in watchers:
                watcher.start()
                started_watchers.append(watcher)
            for root in normalized_incoming:
                self._restore_candidates(root, process_existing)
        except Exception:
            for watcher in reversed(started_watchers):
                try:
                    watcher.stop()
                except Exception:
                    self._logger.exception(
                        "Watcher cleanup failed after automatic startup error",
                        extra={"event": "automatic_start_cleanup_error"},
                    )
            with self._state_lock:
                self._incoming_root = None
                self._incoming_roots = ()
                self._library_root = None
                self._watchers = ()
                self._worker = None
                self._running = False
            raise
        worker.start()
        self._logger.info(
            "Automatic organization started",
            extra={
                "event": "automatic_started",
                "source": "; ".join(str(path) for path in normalized_incoming),
                "count": len(normalized_incoming),
                "destination": str(destination_status.path),
            },
        )
        self._publish_destination(destination_status)
        self._publish()

    def stop(self) -> None:
        with self._state_lock:
            if not self._running:
                return
            watchers = self._watchers
            worker = self._worker
            self._stop_event.set()

        watcher_errors: list[WatcherError] = []
        for watcher in reversed(watchers):
            try:
                watcher.stop()
            except WatcherError as error:
                watcher_errors.append(error)
        if worker is not None:
            worker.join(timeout=5)
            if worker.is_alive():
                raise RuntimeError("Automatic readiness worker did not stop within five seconds.")

        with self._state_lock:
            self._running = False
            self._paused = False
            self._watchers = ()
            self._worker = None
        self._logger.info("Automatic organization stopped", extra={"event": "automatic_stopped"})
        self._publish()
        if watcher_errors:
            raise WatcherError("; ".join(str(error) for error in watcher_errors))

    def pause(self) -> None:
        with self._state_lock:
            if not self._running:
                raise RuntimeError("Automatic organization is not running.")
            self._paused = True
        self._logger.info("Automatic organization paused", extra={"event": "automatic_paused"})
        self._publish()

    def resume(self) -> None:
        with self._state_lock:
            if not self._running:
                raise RuntimeError("Automatic organization is not running.")
            self._paused = False
        self._logger.info("Automatic organization resumed", extra={"event": "automatic_resumed"})
        self._publish()

    def handle_event(
        self,
        event: FileWatchEvent,
        occurred_at: datetime | None = None,
        *,
        incoming_root: Path | None = None,
    ) -> None:
        with self._state_lock:
            roots = self._incoming_roots
            running = self._running
        if not running:
            return
        candidate_path = event.destination or event.source
        selected_root = incoming_root or self._root_for(candidate_path, roots)
        if selected_root is None:
            return
        if self._exclusions is not None and self._exclusions.matches(candidate_path):
            self._registry.remove_path(event.source)
            self._registry.remove_path(candidate_path)
            self._publish()
            return
        timestamp = occurred_at or datetime.now(UTC)
        snapshot: CandidateSnapshot | None = None
        if event.event_type is WatchEventType.DELETED:
            self._registry.remove_path(event.source)
        elif event.event_type is WatchEventType.MOVED and event.destination is not None:
            snapshot = self._registry.rename(
                event.source, event.destination, selected_root, timestamp
            )
        else:
            snapshot = self._registry.register_event(
                event.source,
                selected_root,
                event.event_type.value,
                timestamp,
            )
        if snapshot is not None:
            log_method = (
                self._logger.info
                if event.event_type in {WatchEventType.CREATED, WatchEventType.MOVED}
                else self._logger.debug
            )
            log_method(
                "Candidate filesystem event",
                extra={
                    "event": "candidate_event",
                    "candidate_id": snapshot.candidate_id,
                    "state": snapshot.state.value,
                    "source": str(snapshot.source_path),
                },
            )
        self._publish()

    @staticmethod
    def _root_for(path: Path, roots: tuple[Path, ...]) -> Path | None:
        try:
            parent = path.resolve(strict=False).parent
        except OSError:
            return None
        return next((root for root in roots if parent == root), None)

    def tick(self, now: datetime | None = None) -> None:
        with self._state_lock:
            if not self._running or self._paused:
                return
            library_root = self._library_root
        if library_root is None:
            return
        destination_status = self._destination_resolver(library_root)
        with self._state_lock:
            self._destination_status = destination_status
        self._publish_destination(destination_status)
        if not destination_status.available:
            for candidate in self._registry.snapshots():
                if candidate.state not in {
                    CandidateState.MOVED,
                    CandidateState.FAILED,
                    CandidateState.EXCLUDED,
                    CandidateState.NEEDS_REVIEW,
                }:
                    self._registry.transition(
                        candidate.candidate_id,
                        CandidateState.QUEUED_FOR_DESTINATION,
                        destination_status.detail,
                        category=candidate.proposed_category,
                        destination=candidate.proposed_destination,
                        confidence=candidate.confidence,
                    )
            self._publish()
            return
        observed_at = now or datetime.now(UTC)
        for candidate in self._registry.snapshots():
            self._process(candidate, destination_status.path, observed_at)
        self._publish()

    def _run(self) -> None:
        while not self._stop_event.wait(self._config.sample_interval_seconds):
            try:
                self.tick()
            except Exception:
                self._logger.exception(
                    "Unexpected automatic readiness error",
                    extra={"event": "automatic_worker_error"},
                )

    def _process(
        self, candidate: CandidateSnapshot, library_root: Path, observed_at: datetime
    ) -> None:
        if candidate.state in {
            CandidateState.MOVING,
            CandidateState.MOVED,
            CandidateState.FAILED,
            CandidateState.NEEDS_REVIEW,
            CandidateState.EXCLUDED,
        }:
            return
        if candidate.next_check_at is not None and observed_at < candidate.next_check_at:
            return

        current = self._registry.get(candidate.candidate_id)
        if current is None or current.source_path != candidate.source_path:
            return
        if self._exclusions is not None and self._exclusions.matches(current.source_path):
            self._registry.transition(
                current.candidate_id,
                CandidateState.EXCLUDED,
                "Excluded by settings",
            )
            return
        temporary = self._temporary.evaluate(current.source_path)
        if temporary.outcome is StageOutcome.DEFER:
            self._registry.transition(
                current.candidate_id,
                CandidateState.DOWNLOADING,
                temporary.reason,
            )
            return
        if _is_link_or_junction(current.source_path):
            self._registry.transition(
                current.candidate_id,
                CandidateState.FAILED,
                "Symbolic links and junctions are not processed",
            )
            return

        try:
            file_stat = current.source_path.stat(follow_symlinks=False)
        except FileNotFoundError:
            self._registry.remove_path(current.source_path)
            return
        except OSError as error:
            self._defer(current, observed_at, f"Cannot inspect file: {error}")
            return
        is_file = stat.S_ISREG(file_stat.st_mode)
        is_directory = stat.S_ISDIR(file_stat.st_mode)
        if not is_file and not is_directory:
            self._registry.transition(
                current.candidate_id,
                CandidateState.FAILED,
                "Candidate is neither a regular file nor a directory",
            )
            return

        folder_snapshot = None
        if is_directory:
            try:
                folder_snapshot = snapshot_folder(current.source_path)
            except FolderSnapshotError as error:
                self._defer(current, observed_at, str(error))
                return
            observed_size = folder_snapshot.total_size
            observed_modified = folder_snapshot.modified_token
        else:
            observed_size = file_stat.st_size
            observed_modified = file_stat.st_mtime_ns

        observed, changed = self._registry.observe(
            current.candidate_id,
            observed_size,
            observed_modified,
            observed_at,
        )
        if observed is None:
            return
        if changed:
            self._registry.transition(
                observed.candidate_id,
                CandidateState.STILL_CHANGING,
                "File size or modification time changed; stability restarted",
            )
            return

        stability = self._stability.evaluate(observed, observed_at)
        if stability.outcome is StageOutcome.DEFER:
            self._registry.transition(
                observed.candidate_id,
                CandidateState.COOLING_DOWN,
                stability.reason,
            )
            return

        access_paths = folder_snapshot.files if folder_snapshot is not None else (observed.source_path,)
        for access_path in access_paths:
            try:
                access = self._access_probe.probe(access_path)
            except OSError as error:
                self._defer(
                    observed,
                    observed_at,
                    f"File access probe failed for {access_path.name}: {error}",
                    state=CandidateState.WAITING_FOR_ACCESS,
                )
                return
            if access.outcome is not StageOutcome.PASS:
                self._defer(
                    observed,
                    observed_at,
                    f"{access_path.name}: {access.reason}",
                    state=CandidateState.WAITING_FOR_ACCESS,
                )
                return

        self._registry.transition(observed.candidate_id, CandidateState.READY, stability.reason)
        self._registry.transition(
            observed.candidate_id,
            CandidateState.CLASSIFYING,
            "Applying offline extension classification",
        )
        classification = (
            Classification(
                category="Folders",
                confidence="high",
                reason="Top-level folder candidate routes to Folders",
            )
            if folder_snapshot is not None
            else self._classifier.classify(observed.source_path)
        )
        if classification.disposition is ClassificationDisposition.EXCLUDE:
            self._registry.transition(
                observed.candidate_id,
                CandidateState.EXCLUDED,
                classification.reason,
                confidence=classification.confidence,
            )
            return
        policy_requires_review = (
            self._confidence_policy() is ConfidencePolicy.REVIEW_ALL
        )
        if (
            classification.category is None
            or classification.disposition is ClassificationDisposition.REVIEW
            or classification.confidence not in {"high", "user"}
            or policy_requires_review
        ):
            detail = classification.reason
            if policy_requires_review and classification.category is not None:
                detail = f"Confidence policy requires review; {classification.reason}"
            self._registry.transition(
                observed.candidate_id,
                CandidateState.NEEDS_REVIEW,
                detail,
                category=classification.category,
                confidence=classification.confidence,
            )
            self._logger.info(
                "Automatic candidate needs review",
                extra={
                    "event": "automatic_review",
                    "candidate_id": observed.candidate_id,
                    "state": CandidateState.NEEDS_REVIEW.value,
                    "source": str(observed.source_path),
                    "outcome": "review",
                },
            )
            return

        try:
            proposed = destination_for(
                library_root, classification.category, observed.source_path.name
            )
        except (OSError, ValueError) as error:
            self._defer(
                observed,
                observed_at,
                f"Configured library is unavailable or unsafe: {error}",
            )
            return
        plan_item = PlanItem(
            source=observed.source_path,
            source_root=observed.incoming_root,
            size=observed_size,
            modified_ns=observed_modified,
            status=PlanItemStatus.READY,
            category=classification.category,
            confidence=classification.confidence,
            reason=f"Automatic readiness passed; {classification.reason}",
            proposed_destination=proposed,
            kind=(
                PlanItemKind.DIRECTORY
                if folder_snapshot is not None
                else PlanItemKind.FILE
            ),
            source_fingerprint=(
                folder_snapshot.fingerprint if folder_snapshot is not None else None
            ),
        )
        self._registry.transition(
            observed.candidate_id,
            CandidateState.MOVING,
            "Readiness passed; executing journaled move",
            category=classification.category,
            destination=proposed,
            confidence=classification.confidence,
        )
        result = self._moves.execute(library_root, (plan_item,))[0]
        if result.succeeded:
            self._registry.transition(
                observed.candidate_id,
                CandidateState.MOVED,
                "Automatic move completed and is undoable from History",
                category=classification.category,
                destination=result.destination,
                confidence=classification.confidence,
            )
            return
        latest = self._registry.get(observed.candidate_id)
        if latest is not None:
            self._defer(latest, observed_at, result.message)

    def _defer(
        self,
        candidate: CandidateSnapshot,
        now: datetime,
        detail: str,
        *,
        state: CandidateState = CandidateState.DEFERRED,
    ) -> None:
        exponent = min(candidate.retry_count, 5)
        delay = min(
            self._config.retry_base_seconds * (2**exponent),
            self._config.retry_max_seconds,
        )
        self._registry.transition(
            candidate.candidate_id,
            state,
            detail,
            category=candidate.proposed_category,
            destination=candidate.proposed_destination,
            confidence=candidate.confidence,
            retry=True,
            next_check_at=now + timedelta(seconds=delay),
        )

    def _publish(self) -> None:
        snapshots = self._registry.snapshots()
        with self._state_lock:
            incoming_roots = self._incoming_roots
        if self._state_repository is not None:
            for incoming_root in incoming_roots:
                try:
                    self._state_repository.save_candidates(
                        incoming_root,
                        tuple(
                            candidate
                            for candidate in snapshots
                            if candidate.incoming_root == incoming_root
                        ),
                    )
                except StateError:
                    self._logger.exception(
                        "Candidate queue could not be persisted",
                        extra={
                            "event": "candidate_persistence_error",
                            "source": str(incoming_root),
                        },
                    )
        for callback in tuple(self._callbacks):
            try:
                callback(snapshots)
            except Exception:
                self._logger.exception(
                    "Candidate subscriber failed",
                    extra={"event": "candidate_subscriber_error"},
                )

    def _publish_destination(self, status: DestinationStatus) -> None:
        for callback in tuple(self._destination_callbacks):
            try:
                callback(status)
            except Exception:
                self._logger.exception(
                    "Destination subscriber failed",
                    extra={"event": "destination_subscriber_error"},
                )

    def _restore_candidates(self, incoming_root: Path, process_existing: bool) -> None:
        timestamp = datetime.now(UTC)
        paths: set[Path] = set()
        if self._state_repository is not None:
            paths.update(self._state_repository.pending_paths(incoming_root))
        if process_existing:
            try:
                paths.update(path for path in incoming_root.iterdir())
            except OSError as error:
                raise RuntimeError(
                    f"Cannot scan existing files in incoming folder {incoming_root}: {error}"
                ) from error
        for path in sorted(paths, key=lambda item: str(item).casefold()):
            try:
                if (
                    path.parent.resolve(strict=False) != incoming_root
                    or not (path.is_file() or path.is_dir())
                    or _is_link_or_junction(path)
                    or (self._exclusions is not None and self._exclusions.matches(path))
                ):
                    continue
            except OSError:
                continue
            self._registry.register_event(
                path,
                incoming_root,
                "existing_file" if process_existing else "restored_pending",
                timestamp,
            )
