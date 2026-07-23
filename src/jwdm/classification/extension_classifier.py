"""Conservative built-in extension fallback for the layered classifier."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping

from jwdm.pipeline.models import Classification, ClassificationDisposition


IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".avif",
        ".bmp",
        ".dds",
        ".exr",
        ".gif",
        ".hdr",
        ".heic",
        ".heif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".png",
        ".svg",
        ".tga",
        ".tif",
        ".tiff",
        ".webp",
    }
)


_CATEGORY_EXTENSIONS: Final[dict[str, frozenset[str]]] = {
    "Blender/Projects": frozenset({".blend"}),
    "3D Models": frozenset({".3ds", ".dae", ".fbx", ".glb", ".gltf", ".obj", ".stl"}),
    "Archives": frozenset(
        {".7z", ".bz2", ".cab", ".gz", ".iso", ".rar", ".tar", ".tgz", ".xz", ".zip", ".zst"}
    ),
    "Audio": frozenset(
        {".aac", ".aiff", ".flac", ".m4a", ".mid", ".midi", ".mp3", ".ogg", ".opus", ".wav", ".wma"}
    ),
    "Code": frozenset(
        {
            ".css",
            ".c",
            ".cpp",
            ".cs",
            ".go",
            ".html",
            ".ini",
            ".java",
            ".js",
            ".json",
            ".lua",
            ".php",
            ".ps1",
            ".py",
            ".rs",
            ".sql",
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
            ".epub",
            ".log",
            ".md",
            ".mobi",
            ".ods",
            ".odt",
            ".pdf",
            ".ppt",
            ".pptx",
            ".rtf",
            ".txt",
            ".xls",
            ".xlsx",
            ".xps",
        }
    ),
    "Fonts": frozenset({".otf", ".ttf", ".woff", ".woff2"}),
    "Images": IMAGE_EXTENSIONS,
    "Installers": frozenset({".appx", ".appxbundle", ".exe", ".msi", ".msix", ".msixbundle"}),
    "Installers/TrollStore": frozenset({".tipa"}),
    "Roblox": frozenset({".rbxl", ".rbxlx", ".rbxm", ".rbxmx"}),
    "Cheat Engine/Tables": frozenset({".ct"}),
    "Video": frozenset(
        {".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
    ),
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
