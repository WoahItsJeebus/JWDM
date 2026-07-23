"""Typed models used by the Phase 1 manual organization pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class PlanItemStatus(StrEnum):
    READY = "Ready"
    REVIEW = "Needs review"
    EXCLUDED = "Excluded"


class PlanItemKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class ScanStage(StrEnum):
    DISCOVERING = "discovering"
    CLASSIFYING = "classifying"


class ClassificationDisposition(StrEnum):
    ROUTE = "route"
    REVIEW = "review"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class ScanRoot:
    path: Path
    recursive: bool = False


@dataclass(frozen=True, slots=True)
class Classification:
    category: str | None
    confidence: str
    reason: str
    disposition: ClassificationDisposition = ClassificationDisposition.ROUTE


@dataclass(frozen=True, slots=True)
class PlanItem:
    source: Path
    source_root: Path
    size: int
    modified_ns: int
    status: PlanItemStatus
    category: str | None
    confidence: str
    reason: str
    proposed_destination: Path | None
    collision_behavior: str = "none"
    kind: PlanItemKind = PlanItemKind.FILE
    source_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class ScanIssue:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class ScanProgress:
    stage: ScanStage
    completed_items: int
    total_items: int | None
    current_path: Path


@dataclass(frozen=True, slots=True)
class ScanPlan:
    roots: tuple[ScanRoot, ...]
    library_root: Path
    items: tuple[PlanItem, ...]
    issues: tuple[ScanIssue, ...]
    created_at: datetime

    @property
    def ready_items(self) -> tuple[PlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanItemStatus.READY)

    @property
    def review_items(self) -> tuple[PlanItem, ...]:
        return tuple(item for item in self.items if item.status is PlanItemStatus.REVIEW)

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.items if item.status is not PlanItemStatus.EXCLUDED)
