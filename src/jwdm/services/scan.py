"""Read-only manual folder discovery, progress, and preview-plan creation."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from jwdm.classification.rule_classifier import Classifier
from jwdm.classification.smart_classifier import SmartClassifier
from jwdm.pipeline.models import (
    ClassificationDisposition,
    PlanItem,
    PlanItemStatus,
    ScanIssue,
    ScanPlan,
    ScanProgress,
    ScanRoot,
    ScanStage,
)
from jwdm.services.destinations import destination_for, resolve_collision
from jwdm.services.exclusions import ExclusionMatcher
from jwdm.services.path_validation import PathValidator, ValidatedPaths


ProgressCallback = Callable[[ScanProgress], None]


class _DiscoveredKind(StrEnum):
    FILE = "file"
    EXCLUDED = "excluded"
    LINK = "link"


@dataclass(frozen=True, slots=True)
class _DiscoveredEntry:
    source_root: Path
    path: Path
    kind: _DiscoveredKind


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


class ScanService:
    """Discover and classify selected files without changing the filesystem."""

    def __init__(
        self,
        validator: PathValidator | None = None,
        classifier: Classifier | None = None,
        exclusion_matcher: ExclusionMatcher | None = None,
    ) -> None:
        self._validator = validator or PathValidator()
        self._classifier = classifier or SmartClassifier()
        self._exclusions = exclusion_matcher

    def build_plan(
        self,
        roots: tuple[ScanRoot, ...],
        library_root: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> ScanPlan:
        validated = self._validator.validate(roots, library_root)
        discovered: list[_DiscoveredEntry] = []
        items: list[PlanItem] = []
        issues: list[ScanIssue] = []
        reserved_destinations: set[str] = set()

        for root in validated.roots:
            self._discover_directory(
                validated,
                root,
                root.path,
                discovered,
                issues,
                progress_callback,
            )

        total = len(discovered)
        self._report(
            progress_callback,
            ScanStage.CLASSIFYING,
            0,
            total,
            validated.roots[0].path,
        )
        for index, entry in enumerate(discovered, start=1):
            try:
                items.append(
                    self._plan_discovered(
                        entry,
                        validated.library_root,
                        reserved_destinations,
                    )
                )
            except OSError as error:
                issues.append(ScanIssue(entry.path, f"Cannot inspect path: {error}"))
            if index == 1 or index == total or index % 10 == 0:
                self._report(
                    progress_callback,
                    ScanStage.CLASSIFYING,
                    index,
                    total,
                    entry.path,
                )

        items.sort(key=lambda item: str(item.source).casefold())
        return ScanPlan(
            roots=validated.roots,
            library_root=validated.library_root,
            items=tuple(items),
            issues=tuple(issues),
            created_at=datetime.now(UTC),
        )

    def _discover_directory(
        self,
        validated: ValidatedPaths,
        scan_root: ScanRoot,
        directory: Path,
        discovered: list[_DiscoveredEntry],
        issues: list[ScanIssue],
        progress_callback: ProgressCallback | None,
    ) -> None:
        if self._exclusions is not None and self._exclusions.matches(directory):
            return
        if directory == validated.library_root or directory.is_relative_to(
            validated.library_root
        ):
            return
        self._report(
            progress_callback,
            ScanStage.DISCOVERING,
            len(discovered),
            None,
            directory,
        )
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError as error:
            issues.append(ScanIssue(directory, f"Cannot read folder: {error}"))
            return

        for entry in entries:
            path = Path(entry.path)
            try:
                kind: _DiscoveredKind | None = None
                if self._exclusions is not None and self._exclusions.matches(path):
                    if entry.is_file(follow_symlinks=False):
                        kind = _DiscoveredKind.EXCLUDED
                elif _is_link_or_junction(path):
                    kind = _DiscoveredKind.LINK
                elif entry.is_file(follow_symlinks=False):
                    kind = _DiscoveredKind.FILE
                elif scan_root.recursive and entry.is_dir(follow_symlinks=False):
                    self._discover_directory(
                        validated,
                        scan_root,
                        path,
                        discovered,
                        issues,
                        progress_callback,
                    )
                if kind is not None:
                    discovered.append(_DiscoveredEntry(scan_root.path, path, kind))
                    count = len(discovered)
                    if count == 1 or count % 25 == 0:
                        self._report(
                            progress_callback,
                            ScanStage.DISCOVERING,
                            count,
                            None,
                            path,
                        )
            except OSError as error:
                issues.append(ScanIssue(path, f"Cannot inspect path: {error}"))

    def _plan_discovered(
        self,
        entry: _DiscoveredEntry,
        library_root: Path,
        reserved_destinations: set[str],
    ) -> PlanItem:
        if entry.kind is _DiscoveredKind.LINK:
            return PlanItem(
                source=entry.path,
                source_root=entry.source_root,
                size=0,
                modified_ns=0,
                status=PlanItemStatus.EXCLUDED,
                category=None,
                confidence="none",
                reason="Symbolic links and junctions are excluded",
                proposed_destination=None,
            )
        if entry.kind is _DiscoveredKind.EXCLUDED:
            file_stat = entry.path.stat(follow_symlinks=False)
            return PlanItem(
                source=entry.path,
                source_root=entry.source_root,
                size=file_stat.st_size,
                modified_ns=file_stat.st_mtime_ns,
                status=PlanItemStatus.EXCLUDED,
                category=None,
                confidence="user",
                reason="Excluded by settings",
                proposed_destination=None,
            )
        return self._plan_file(
            entry.source_root,
            entry.path,
            library_root,
            reserved_destinations,
        )

    def _plan_file(
        self,
        source_root: Path,
        path: Path,
        library_root: Path,
        reserved_destinations: set[str],
    ) -> PlanItem:
        file_stat = path.stat(follow_symlinks=False)
        classification = self._classifier.classify(path)
        if classification.disposition is ClassificationDisposition.EXCLUDE:
            return PlanItem(
                source=path,
                source_root=source_root,
                size=file_stat.st_size,
                modified_ns=file_stat.st_mtime_ns,
                status=PlanItemStatus.EXCLUDED,
                category=None,
                confidence=classification.confidence,
                reason=classification.reason,
                proposed_destination=None,
            )
        if (
            classification.category is None
            or classification.disposition is ClassificationDisposition.REVIEW
        ):
            return PlanItem(
                source=path,
                source_root=source_root,
                size=file_stat.st_size,
                modified_ns=file_stat.st_mtime_ns,
                status=PlanItemStatus.REVIEW,
                category=classification.category,
                confidence=classification.confidence,
                reason=classification.reason,
                proposed_destination=None,
            )

        base_destination = destination_for(
            library_root, classification.category, path.name
        )
        proposed_destination, collision_behavior = resolve_collision(
            base_destination, reserved_destinations
        )
        reason = classification.reason
        if collision_behavior != "none":
            reason = f"{reason}; existing name retained with safe numbering"
        return PlanItem(
            source=path,
            source_root=source_root,
            size=file_stat.st_size,
            modified_ns=file_stat.st_mtime_ns,
            status=PlanItemStatus.READY,
            category=classification.category,
            confidence=classification.confidence,
            reason=reason,
            proposed_destination=proposed_destination,
            collision_behavior=collision_behavior,
        )

    @staticmethod
    def _report(
        callback: ProgressCallback | None,
        stage: ScanStage,
        completed: int,
        total: int | None,
        current_path: Path,
    ) -> None:
        if callback is not None:
            callback(ScanProgress(stage, completed, total, current_path))
