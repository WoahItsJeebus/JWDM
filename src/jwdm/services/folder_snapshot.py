"""Safe, deterministic snapshots for top-level folder candidates."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path


class FolderSnapshotError(RuntimeError):
    """A directory tree could not be inspected without following unsafe entries."""


@dataclass(frozen=True, slots=True)
class FolderSnapshot:
    total_size: int
    modified_token: int
    fingerprint: str
    files: tuple[Path, ...]


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def snapshot_folder(root: Path) -> FolderSnapshot:
    """Describe a regular directory tree without reading file contents."""

    if _is_link_or_junction(root) or not root.is_dir():
        raise FolderSnapshotError(f"Folder is unavailable or is a link/junction: {root}")
    digest = hashlib.sha256()
    files: list[Path] = []
    total_size = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name.casefold())
        except OSError as error:
            raise FolderSnapshotError(f"Cannot read folder {directory}: {error}") from error
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if _is_link_or_junction(path):
                raise FolderSnapshotError(
                    f"Folders containing symbolic links or junctions are not moved: {path}"
                )
            try:
                current = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise FolderSnapshotError(f"Cannot inspect {path}: {error}") from error
            if stat.S_ISDIR(current.st_mode):
                digest.update(f"D\0{relative}\0".encode("utf-8", errors="surrogatepass"))
                stack.append(path)
            elif stat.S_ISREG(current.st_mode):
                files.append(path)
                total_size += current.st_size
                digest.update(
                    f"F\0{relative}\0{current.st_size}\0{current.st_mtime_ns}\0".encode(
                        "utf-8", errors="surrogatepass"
                    )
                )
            else:
                raise FolderSnapshotError(f"Folder contains an unsupported entry: {path}")
    fingerprint = digest.hexdigest()
    modified_token = int.from_bytes(digest.digest()[:8], "big", signed=False)
    return FolderSnapshot(total_size, modified_token, fingerprint, tuple(files))
