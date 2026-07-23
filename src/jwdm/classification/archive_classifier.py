"""Safe ZIP metadata inspection without extraction or execution."""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from jwdm.pipeline.models import Classification, ClassificationDisposition


_DRIVE_PREFIX: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z]:")
_CODE_MARKERS: Final[frozenset[str]] = frozenset(
    {"cargo.toml", "go.mod", "package.json", "pyproject.toml"}
)
_MINECRAFT_MARKERS: Final[frozenset[str]] = frozenset(
    {"fabric.mod.json", "mcmod.info", "meta-inf/mods.toml", "plugin.yml"}
)


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int = 10_000
    max_member_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_compression_ratio: float = 200.0
    max_name_length: int = 1_024
    max_preview_bytes: int = 256 * 1024


class ArchiveClassifier:
    """Inspect ZIP central-directory metadata and a bounded addon marker file."""

    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self._limits = limits or ArchiveLimits()

    def classify(self, path: Path) -> Classification | None:
        if path.suffix.casefold() != ".zip":
            return None
        try:
            with zipfile.ZipFile(path, mode="r", allowZip64=True) as archive:
                members = archive.infolist()
                problem = self._validate_members(members)
                if problem is not None:
                    return self._review(problem)
                normalized = tuple(self._normalize_name(member.filename) for member in members)
                return self._classify_signals(path, archive, members, normalized)
        except FileNotFoundError:
            return None
        except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            return self._review(
                f"ZIP metadata was not safely readable ({type(error).__name__})"
            )

    def _validate_members(self, members: list[zipfile.ZipInfo]) -> str | None:
        if len(members) > self._limits.max_members:
            return (
                f"ZIP contains {len(members):,} members; limit is "
                f"{self._limits.max_members:,}"
            )
        total = 0
        for member in members:
            name = member.filename
            if len(name) > self._limits.max_name_length:
                return "ZIP contains an overlong member name"
            normalized = self._normalize_name(name)
            parts = PurePosixPath(normalized).parts
            if (
                not normalized
                or normalized.startswith("/")
                or _DRIVE_PREFIX.match(normalized)
                or ".." in parts
                or "\x00" in normalized
            ):
                return f"ZIP contains an unsafe member path: {name!r}"
            if member.flag_bits & 0x1:
                return "ZIP is password-protected or contains encrypted members"
            if member.file_size > self._limits.max_member_bytes:
                return f"ZIP member exceeds the inspection size limit: {name!r}"
            total += member.file_size
            if total > self._limits.max_total_bytes:
                return "ZIP advertised uncompressed size exceeds the inspection limit"
            if member.file_size and not member.is_dir():
                ratio = member.file_size / max(member.compress_size, 1)
                if ratio > self._limits.max_compression_ratio:
                    return f"ZIP member has a suspicious compression ratio: {name!r}"
        return None

    def _classify_signals(
        self,
        path: Path,
        archive: zipfile.ZipFile,
        members: list[zipfile.ZipInfo],
        names: tuple[str, ...],
    ) -> Classification:
        lowered = tuple(name.casefold().strip("/") for name in names)
        basenames = frozenset(PurePosixPath(name).name for name in lowered if name)

        for member, name in zip(members, lowered, strict=True):
            if not name.endswith("/__init__.py") and name != "__init__.py":
                continue
            if member.file_size > self._limits.max_preview_bytes:
                continue
            try:
                with archive.open(member, mode="r") as stream:
                    preview = stream.read(self._limits.max_preview_bytes + 1)
            except (NotImplementedError, OSError, RuntimeError, zipfile.BadZipFile):
                return self._review("ZIP addon marker could not be read safely")
            if len(preview) <= self._limits.max_preview_bytes and b"bl_info" in preview:
                return Classification(
                    category="Blender/Addons",
                    confidence="high",
                    reason="ZIP metadata and bounded __init__.py preview found Blender bl_info",
                )

        if any(
            self._contains_marker(lowered, marker)
            or ("/" not in marker and PurePosixPath(marker).name in basenames)
            for marker in _MINECRAFT_MARKERS
        ):
            return Classification(
                category="Minecraft",
                confidence="high",
                reason="ZIP metadata contains a recognized Minecraft package marker",
            )
        if any(
            name.endswith(".project.json")
            or name.endswith((".rbxl", ".rbxlx", ".rbxm", ".rbxmx"))
            for name in lowered
        ):
            return Classification(
                category="Roblox",
                confidence="high",
                reason="ZIP metadata contains a recognized Roblox/Rojo project marker",
            )
        if basenames.intersection(_CODE_MARKERS):
            return Classification(
                category="Code",
                confidence="high",
                reason="ZIP metadata contains a recognized source-project marker",
            )

        normalized_filename = re.sub(r"[^a-z0-9]+", " ", path.stem.casefold()).strip()
        if "texture pack" in normalized_filename:
            return Classification(
                category="Images/Textures",
                confidence="high",
                reason="ZIP filename contains the strong 'Texture Pack' signal",
            )

        segments = {part for name in lowered for part in PurePosixPath(name).parts}
        if "textures" in segments and {"materials", "models"}.intersection(segments):
            return Classification(
                category="3D Models",
                confidence="medium",
                reason="ZIP metadata suggests a model/texture asset pack; review before routing",
                disposition=ClassificationDisposition.REVIEW,
            )
        return Classification(
            category="Archives",
            confidence="high",
            reason=f"ZIP metadata inspected safely ({len(members):,} members) -> Archives",
        )

    @staticmethod
    def _normalize_name(name: str) -> str:
        return name.replace("\\", "/")

    @staticmethod
    def _contains_marker(names: tuple[str, ...], marker: str) -> bool:
        return any(name == marker or name.endswith(f"/{marker}") for name in names)

    @staticmethod
    def _review(detail: str) -> Classification:
        return Classification(
            category="Archives",
            confidence="low",
            reason=f"Archive inspection requires review: {detail}",
            disposition=ClassificationDisposition.REVIEW,
        )
