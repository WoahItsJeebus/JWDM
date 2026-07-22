from __future__ import annotations

from pathlib import Path

from jwdm.services.volumes import VolumeDescriptor, VolumeService


class _Backend:
    def __init__(self) -> None:
        self.descriptors: dict[Path, VolumeDescriptor] = {}
        self.mounts: dict[str, tuple[Path, ...]] = {}
        self.free = 8 * 1024**3

    def describe(self, path: Path) -> VolumeDescriptor:
        resolved = path.resolve(strict=True)
        for root, descriptor in self.descriptors.items():
            if resolved == root or resolved.is_relative_to(root):
                return descriptor
        raise OSError(f"No simulated volume for {path}")

    def mount_paths(self, volume_id: str) -> tuple[Path, ...]:
        return self.mounts.get(volume_id, ())

    def free_bytes(self, path: Path) -> int:
        self.describe(path)
        return self.free


def test_bound_volume_reconnects_at_new_mount_and_rejects_reused_path(
    tmp_path: Path,
) -> None:
    old_mount = tmp_path / "old-drive"
    new_mount = tmp_path / "new-drive"
    old_library = old_mount / "JWDM" / "Library"
    new_library = new_mount / "JWDM" / "Library"
    old_library.mkdir(parents=True)
    new_library.mkdir(parents=True)
    backend = _Backend()
    expected = VolumeDescriptor("volume-A", old_mount, 123, "NTFS", "Assets")
    backend.descriptors[old_mount.resolve()] = expected
    service = VolumeService(backend)
    binding = service.bind(old_library)

    old_library.rmdir()
    (old_mount / "JWDM").rmdir()
    backend.descriptors[old_mount.resolve()] = VolumeDescriptor(
        "volume-B", old_mount, 999, "NTFS", "Other disk"
    )
    backend.descriptors[new_mount.resolve()] = VolumeDescriptor(
        "volume-A", new_mount, 123, "NTFS", "Assets"
    )
    backend.mounts["volume-A"] = (new_mount,)

    status = service.resolve(old_library, binding)

    assert status.available
    assert status.path == new_library.resolve()
    assert status.volume_id == "volume-A"
    assert "Assets" in status.detail


def test_disconnected_bound_volume_never_falls_back(tmp_path: Path) -> None:
    mount = tmp_path / "drive"
    library = mount / "Library"
    library.mkdir(parents=True)
    backend = _Backend()
    backend.descriptors[mount.resolve()] = VolumeDescriptor(
        "volume-A", mount, 123, "NTFS", "Assets"
    )
    service = VolumeService(backend)
    binding = service.bind(library)
    library.rmdir()

    status = service.resolve(library, binding)

    assert not status.available
    assert status.path == library
    assert status.volume_id == "volume-A"
    assert "disconnected" in status.detail
