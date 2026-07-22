"""Conservative built-in extension classifier for Phase 1."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping

from jwdm.pipeline.models import Classification, ClassificationDisposition


_CATEGORY_EXTENSIONS: Final[dict[str, frozenset[str]]] = {
    "Blender/Projects": frozenset({".blend"}),
    "3D Models": frozenset({".3ds", ".dae", ".fbx", ".glb", ".gltf", ".obj", ".stl"}),
    "Archives": frozenset({".7z", ".bz2", ".gz", ".rar", ".tar", ".xz", ".zip"}),
    "Audio": frozenset({".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav"}),
    "Code": frozenset(
        {
            ".css",
            ".html",
            ".ini",
            ".js",
            ".json",
            ".ps1",
            ".py",
            ".toml",
            ".ts",
            ".xml",
            ".yaml",
            ".yml",
        }
    ),
    "Documents": frozenset(
        {
            ".csv",
            ".doc",
            ".docx",
            ".md",
            ".ods",
            ".odt",
            ".pdf",
            ".ppt",
            ".pptx",
            ".rtf",
            ".txt",
            ".xls",
            ".xlsx",
        }
    ),
    "Fonts": frozenset({".otf", ".ttf", ".woff", ".woff2"}),
    "Images": frozenset(
        {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp"}
    ),
    "Installers": frozenset({".appx", ".exe", ".msi", ".msix"}),
    "Video": frozenset({".avi", ".mkv", ".mov", ".mp4", ".webm", ".wmv"}),
}


def _build_extension_map() -> Mapping[str, str]:
    mapping: dict[str, str] = {}
    for category, extensions in _CATEGORY_EXTENSIONS.items():
        for extension in extensions:
            if extension in mapping:
                raise RuntimeError(f"Duplicate built-in extension: {extension}")
            mapping[extension] = category
    return MappingProxyType(mapping)


EXTENSION_CATEGORIES: Final[Mapping[str, str]] = _build_extension_map()


class ExtensionClassifier:
    """Classify by filename extension without inspecting or executing content."""

    def classify(self, path: Path) -> Classification:
        extension = path.suffix.casefold()
        category = EXTENSION_CATEGORIES.get(extension)
        if category is None:
            display_extension = extension if extension else "no extension"
            return Classification(
                category=None,
                confidence="unknown",
                reason=f"No built-in rule for {display_extension}",
                disposition=ClassificationDisposition.REVIEW,
            )
        return Classification(
            category=category,
            confidence="high",
            reason=f"Built-in extension rule: {extension} → {category}",
        )
