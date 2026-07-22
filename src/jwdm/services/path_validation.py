"""Manual-scan source and library path validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jwdm.pipeline.models import ScanRoot


class PathValidationError(ValueError):
    """A selected source/library relationship is unsafe or unsupported."""


@dataclass(frozen=True, slots=True)
class ValidatedPaths:
    roots: tuple[ScanRoot, ...]
    library_root: Path


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _is_network_path(path: Path) -> bool:
    return str(path).startswith("\\\\")


def _contains(parent: Path, candidate: Path) -> bool:
    return candidate == parent or candidate.is_relative_to(parent)


class PathValidator:
    """Normalize paths and reject relationships that could recurse or duplicate work."""

    def validate(self, roots: tuple[ScanRoot, ...], library_root: Path) -> ValidatedPaths:
        if not roots:
            raise PathValidationError("Select at least one source folder.")

        normalized_library = self._existing_directory(library_root, "Library")
        normalized_roots = tuple(
            ScanRoot(self._existing_directory(root.path, "Source"), root.recursive)
            for root in roots
        )

        for index, root in enumerate(normalized_roots):
            if root.path == normalized_library:
                raise PathValidationError("A source folder and the library cannot be the same folder.")
            if _contains(normalized_library, root.path):
                raise PathValidationError(
                    f"Source folder is inside the managed library: {root.path}"
                )
            for other in normalized_roots[index + 1 :]:
                if _contains(root.path, other.path) or _contains(other.path, root.path):
                    raise PathValidationError(
                        f"Source folders overlap and would scan files twice: {root.path} and {other.path}"
                    )

        return ValidatedPaths(normalized_roots, normalized_library)

    def validate_automatic(
        self,
        incoming_root: Path,
        configured_library: Path,
        available_library: Path | None,
    ) -> ValidatedPaths:
        """Validate monitoring even while a previously bound library is disconnected."""

        if available_library is not None:
            return self.validate(
                (ScanRoot(incoming_root, False),),
                available_library,
            )
        normalized_incoming = self._existing_directory(incoming_root, "Incoming")
        expanded_library = configured_library.expanduser()
        if _is_network_path(expanded_library):
            raise PathValidationError(
                f"Library network paths are not supported: {configured_library}"
            )
        normalized_library = expanded_library.resolve(strict=False)
        if normalized_incoming == normalized_library:
            raise PathValidationError(
                "The incoming folder and disconnected library path cannot be identical."
            )
        if _contains(normalized_library, normalized_incoming):
            raise PathValidationError(
                f"Incoming folder is inside the configured library: {incoming_root}"
            )
        return ValidatedPaths(
            (ScanRoot(normalized_incoming, False),),
            normalized_library,
        )

    @staticmethod
    def _existing_directory(path: Path, label: str) -> Path:
        expanded = path.expanduser()
        if _is_network_path(expanded):
            raise PathValidationError(f"{label} network paths are not supported: {path}")
        if not expanded.exists():
            raise PathValidationError(f"{label} folder does not exist: {path}")
        if not expanded.is_dir():
            raise PathValidationError(f"{label} path is not a folder: {path}")
        if _is_link_or_junction(expanded):
            raise PathValidationError(f"{label} folder cannot be a symbolic link or junction: {path}")
        try:
            return expanded.resolve(strict=True)
        except OSError as error:
            raise PathValidationError(f"Cannot resolve {label.lower()} folder {path}: {error}") from error
