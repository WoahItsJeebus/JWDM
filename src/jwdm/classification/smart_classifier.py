"""Layered Phase 5 offline classification."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from jwdm.classification.archive_classifier import ArchiveClassifier
from jwdm.classification.extension_classifier import ExtensionClassifier
from jwdm.classification.image_classifier import ImageMetadataClassifier
from jwdm.classification.texture_classifier import TextureNameClassifier
from jwdm.pipeline.models import Classification


class ConditionalClassifier(Protocol):
    def classify(self, path: Path) -> Classification | None: ...


class SmartClassifier:
    """Apply safe content/name signals before the broad extension fallback."""

    def __init__(
        self,
        inspectors: tuple[ConditionalClassifier, ...] | None = None,
        fallback: ExtensionClassifier | None = None,
    ) -> None:
        self._inspectors = inspectors or (
            ArchiveClassifier(),
            TextureNameClassifier(),
            ImageMetadataClassifier(),
        )
        self._fallback = fallback or ExtensionClassifier()

    def classify(self, path: Path) -> Classification:
        for inspector in self._inspectors:
            result = inspector.classify(path)
            if result is not None:
                return result
        return self._fallback.classify(path)
