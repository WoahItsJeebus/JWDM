"""Read-only manual folder scanning and preview-plan creation."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from jwdm.classification.extension_classifier import ExtensionClassifier
from jwdm.classification.rule_classifier import Classifier
from jwdm.pipeline.models import (
    ClassificationDisposition,
    PlanItem,
    PlanItemStatus,
    ScanIssue,
    ScanPlan,
    ScanRoot,
)
from jwdm.services.destinations import destination_for, resolve_collision
from jwdm.services.exclusions import ExclusionMatcher
from jwdm.services.path_validation import PathValidator, ValidatedPaths


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


class ScanService:
    """Scan selected roots without changing any files or directories."""

    def __init__(
        self,
        validator: PathValidator | None = None,
        classifier: Classifier | None = None,
        exclusion_matcher: ExclusionMatcher | None = None,
    ) -> None:
        self._validator = validator or PathValidator()
        self._classifier = classifier or ExtensionClassifier()
        self._exclusions = exclusion_matcher

    def build_plan(self, roots: tuple[ScanRoot, ...], library_root: Path) -> ScanPlan:
        validated = self._validator.validate(roots, library_root)
        items: list[PlanItem] = []
        issues: list[ScanIssue] = []
        reserved_destinations: set[str] = set()

        for root in validated.roots:
            self._scan_directory(
                validated,
                root,
                root.path,
                items,
                issues,
                reserved_destinations,
            )

        items.sort(key=lambda item: str(item.source).casefold())
        return ScanPlan(
            roots=validated.roots,
            library_root=validated.library_root,
            items=tuple(items),
            issues=tuple(issues),
            created_at=datetime.now(UTC),
        )

    def _scan_directory(
        self,
        validated: ValidatedPaths,
        scan_root: ScanRoot,
        directory: Path,
        items: list[PlanItem],
        issues: list[ScanIssue],
        reserved_destinations: set[str],
    ) -> None:
        if self._exclusions is not None and self._exclusions.matches(directory):
            return
        if directory == validated.library_root or directory.is_relative_to(validated.library_root):
            return
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError as error:
            issues.append(ScanIssue(directory, f"Cannot read folder: {error}"))
            return

        for entry in entries:
            path = Path(entry.path)
            try:
                if self._exclusions is not None and self._exclusions.matches(path):
                    if entry.is_file(follow_symlinks=False):
                        file_stat = path.stat(follow_symlinks=False)
                        items.append(
                            PlanItem(
                                source=path,
                                source_root=scan_root.path,
                                size=file_stat.st_size,
                                modified_ns=file_stat.st_mtime_ns,
                                status=PlanItemStatus.EXCLUDED,
                                category=None,
                                confidence="user",
                                reason="Excluded by settings",
                                proposed_destination=None,
                            )
                        )
                elif _is_link_or_junction(path):
                    items.append(
                        PlanItem(
                            source=path,
                            source_root=scan_root.path,
                            size=0,
                            modified_ns=0,
                            status=PlanItemStatus.EXCLUDED,
                            category=None,
                            confidence="none",
                            reason="Symbolic links and junctions are excluded",
                            proposed_destination=None,
                        )
                    )
                elif entry.is_file(follow_symlinks=False):
                    items.append(
                        self._plan_file(
                            scan_root.path,
                            path,
                            validated.library_root,
                            reserved_destinations,
                        )
                    )
                elif scan_root.recursive and entry.is_dir(follow_symlinks=False):
                    self._scan_directory(
                        validated,
                        scan_root,
                        path,
                        items,
                        issues,
                        reserved_destinations,
                    )
            except OSError as error:
                issues.append(ScanIssue(path, f"Cannot inspect path: {error}"))

    def _plan_file(
        self,
        source_root: Path,
        path: Path,
        library_root: Path,
        reserved_destinations: set[str],
    ) -> PlanItem:
        stat = path.stat(follow_symlinks=False)
        classification = self._classifier.classify(path)
        if classification.disposition is ClassificationDisposition.EXCLUDE:
            return PlanItem(
                source=path,
                source_root=source_root,
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
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
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                status=PlanItemStatus.REVIEW,
                category=None,
                confidence=classification.confidence,
                reason=classification.reason,
                proposed_destination=None,
            )

        base_destination = destination_for(library_root, classification.category, path.name)
        proposed_destination, collision_behavior = resolve_collision(
            base_destination, reserved_destinations
        )
        reason = classification.reason
        if collision_behavior != "none":
            reason = f"{reason}; existing name retained with safe numbering"
        return PlanItem(
            source=path,
            source_root=source_root,
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            status=PlanItemStatus.READY,
            category=classification.category,
            confidence=classification.confidence,
            reason=reason,
            proposed_destination=proposed_destination,
            collision_behavior=collision_behavior,
        )
