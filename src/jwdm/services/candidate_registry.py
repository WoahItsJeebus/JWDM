"""Thread-safe in-memory candidate deduplication and state registry."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jwdm.pipeline.candidate import CandidateSnapshot, CandidateState


@dataclass(slots=True)
class _Candidate:
    candidate_id: str
    source_path: Path
    incoming_root: Path
    first_seen_at: datetime
    last_event_at: datetime
    last_size: int | None = None
    last_modified_ns: int | None = None
    stable_since: datetime | None = None
    stable_samples: int = 0
    state: CandidateState = CandidateState.DETECTED
    signals: list[str] = field(default_factory=list)
    proposed_category: str | None = None
    proposed_destination: Path | None = None
    confidence: str | None = None
    retry_count: int = 0
    next_check_at: datetime | None = None
    detail: str = "New filesystem candidate"


_REPLACEABLE_STATES = {CandidateState.MOVED, CandidateState.FAILED}


def _identity(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


class CandidateRegistry:
    """Maintain one active candidate per normalized source path."""

    def __init__(self) -> None:
        self._by_id: dict[str, _Candidate] = {}
        self._path_to_id: dict[str, str] = {}
        self._lock = threading.RLock()

    def register_event(
        self, path: Path, incoming_root: Path, signal: str, occurred_at: datetime
    ) -> CandidateSnapshot:
        identity = _identity(path)
        with self._lock:
            existing_id = self._path_to_id.get(identity)
            existing = self._by_id.get(existing_id) if existing_id else None
            if existing is None or existing.state in _REPLACEABLE_STATES:
                candidate = _Candidate(
                    candidate_id=str(uuid.uuid4()),
                    source_path=path,
                    incoming_root=incoming_root,
                    first_seen_at=occurred_at,
                    last_event_at=occurred_at,
                )
                self._by_id[candidate.candidate_id] = candidate
                self._path_to_id[identity] = candidate.candidate_id
            else:
                candidate = existing
                candidate.last_event_at = occurred_at
                if candidate.state is not CandidateState.MOVING:
                    candidate.state = CandidateState.DETECTED
                    candidate.detail = f"Filesystem event: {signal}"
                    candidate.next_check_at = None
            self._add_signal(candidate, signal)
            return self._snapshot(candidate)

    def rename(
        self,
        source: Path,
        destination: Path,
        incoming_root: Path,
        occurred_at: datetime,
    ) -> CandidateSnapshot:
        source_identity = _identity(source)
        destination_identity = _identity(destination)
        with self._lock:
            candidate_id = self._path_to_id.pop(source_identity, None)
            candidate = self._by_id.get(candidate_id) if candidate_id else None
            if candidate is None:
                return self.register_event(destination, incoming_root, "moved_in", occurred_at)
            replaced_id = self._path_to_id.get(destination_identity)
            if replaced_id is not None and replaced_id != candidate.candidate_id:
                self._by_id.pop(replaced_id, None)
            candidate.source_path = destination
            candidate.last_event_at = occurred_at
            candidate.last_size = None
            candidate.last_modified_ns = None
            candidate.stable_since = None
            candidate.stable_samples = 0
            candidate.state = CandidateState.DETECTED
            candidate.detail = f"Renamed from {source.name}"
            candidate.next_check_at = None
            self._add_signal(candidate, "renamed")
            self._path_to_id[destination_identity] = candidate.candidate_id
            return self._snapshot(candidate)

    def remove_path(self, path: Path) -> None:
        with self._lock:
            candidate_id = self._path_to_id.pop(_identity(path), None)
            if candidate_id is not None:
                candidate = self._by_id.get(candidate_id)
                if candidate is not None and candidate.state is not CandidateState.MOVING:
                    self._by_id.pop(candidate_id, None)

    def clear(self) -> None:
        with self._lock:
            self._by_id.clear()
            self._path_to_id.clear()

    def get(self, candidate_id: str) -> CandidateSnapshot | None:
        with self._lock:
            candidate = self._by_id.get(candidate_id)
            return self._snapshot(candidate) if candidate is not None else None

    def snapshots(self) -> tuple[CandidateSnapshot, ...]:
        with self._lock:
            candidates = sorted(self._by_id.values(), key=lambda item: item.first_seen_at)
            return tuple(self._snapshot(candidate) for candidate in candidates)

    def observe(
        self, candidate_id: str, size: int, modified_ns: int, observed_at: datetime
    ) -> tuple[CandidateSnapshot | None, bool]:
        with self._lock:
            candidate = self._by_id.get(candidate_id)
            if candidate is None:
                return None, False
            changed = (
                candidate.last_size is not None
                and (candidate.last_size != size or candidate.last_modified_ns != modified_ns)
            )
            if candidate.last_size is None or changed:
                candidate.stable_samples = 1
                candidate.stable_since = observed_at
            else:
                candidate.stable_samples += 1
            candidate.last_size = size
            candidate.last_modified_ns = modified_ns
            candidate.next_check_at = None
            return self._snapshot(candidate), changed

    def transition(
        self,
        candidate_id: str,
        state: CandidateState,
        detail: str,
        *,
        category: str | None = None,
        destination: Path | None = None,
        confidence: str | None = None,
        retry: bool = False,
        next_check_at: datetime | None = None,
    ) -> CandidateSnapshot | None:
        with self._lock:
            candidate = self._by_id.get(candidate_id)
            if candidate is None:
                return None
            candidate.state = state
            candidate.detail = detail
            candidate.proposed_category = category
            candidate.proposed_destination = destination
            candidate.confidence = confidence
            candidate.next_check_at = next_check_at
            if retry:
                candidate.retry_count += 1
            elif state in {CandidateState.READY, CandidateState.MOVED}:
                candidate.retry_count = 0
            return self._snapshot(candidate)

    @staticmethod
    def _add_signal(candidate: _Candidate, signal: str) -> None:
        candidate.signals.append(signal)
        if len(candidate.signals) > 20:
            del candidate.signals[:-20]

    @staticmethod
    def _snapshot(candidate: _Candidate) -> CandidateSnapshot:
        return CandidateSnapshot(
            candidate_id=candidate.candidate_id,
            source_path=candidate.source_path,
            incoming_root=candidate.incoming_root,
            first_seen_at=candidate.first_seen_at,
            last_event_at=candidate.last_event_at,
            last_size=candidate.last_size,
            last_modified_ns=candidate.last_modified_ns,
            stable_since=candidate.stable_since,
            stable_samples=candidate.stable_samples,
            state=candidate.state,
            signals=tuple(candidate.signals),
            proposed_category=candidate.proposed_category,
            proposed_destination=candidate.proposed_destination,
            confidence=candidate.confidence,
            retry_count=candidate.retry_count,
            next_check_at=candidate.next_check_at,
            detail=candidate.detail,
        )

