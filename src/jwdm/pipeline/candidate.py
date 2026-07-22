"""Candidate state exposed by the automatic readiness pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class CandidateState(StrEnum):
    DETECTED = "Detected"
    DOWNLOADING = "Downloading"
    STILL_CHANGING = "Still changing"
    WAITING_FOR_ACCESS = "Waiting for file access"
    COOLING_DOWN = "Cooling down"
    READY = "Ready"
    CLASSIFYING = "Classifying"
    NEEDS_REVIEW = "Needs review"
    EXCLUDED = "Excluded"
    QUEUED_FOR_DESTINATION = "Queued for destination"
    MOVING = "Moving"
    MOVED = "Moved"
    DEFERRED = "Deferred"
    FAILED = "Failed"


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    candidate_id: str
    source_path: Path
    incoming_root: Path
    first_seen_at: datetime
    last_event_at: datetime
    last_size: int | None
    last_modified_ns: int | None
    stable_since: datetime | None
    stable_samples: int
    state: CandidateState
    signals: tuple[str, ...]
    proposed_category: str | None
    proposed_destination: Path | None
    confidence: str | None
    retry_count: int
    next_check_at: datetime | None
    detail: str
