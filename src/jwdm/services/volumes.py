"""Volume identity, free-space, and reconnect resolution services."""

from __future__ import annotations

import ctypes
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jwdm.config import VolumeBinding


@dataclass(frozen=True, slots=True)
class VolumeDescriptor:
    volume_id: str
    mount_path: Path
    serial_number: int | None
    filesystem: str | None
    label: str | None


@dataclass(frozen=True, slots=True)
class DestinationStatus:
    available: bool
    path: Path
    volume_id: str | None
    free_bytes: int | None
    detail: str


class VolumeBackend(Protocol):
    def describe(self, path: Path) -> VolumeDescriptor: ...

    def mount_paths(self, volume_id: str) -> tuple[Path, ...]: ...

    def free_bytes(self, path: Path) -> int: ...


class SystemVolumeBackend:
    """Use Windows volume GUIDs, with a stat-device fallback for tests/ports."""

    def describe(self, path: Path) -> VolumeDescriptor:
        resolved = path.resolve(strict=True)
        if os.name != "nt":
            root = Path(resolved.anchor or os.sep)
            return VolumeDescriptor(
                volume_id=f"device:{resolved.stat().st_dev}",
                mount_path=root,
                serial_number=None,
                filesystem=None,
                label=None,
            )
        return self._describe_windows(resolved)

    def mount_paths(self, volume_id: str) -> tuple[Path, ...]:
        if os.name != "nt" or not volume_id.startswith("\\\\?\\Volume{"):
            return ()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_paths = kernel32.GetVolumePathNamesForVolumeNameW
        get_paths.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_paths.restype = ctypes.c_int
        volume_name = volume_id if volume_id.endswith("\\") else f"{volume_id}\\"
        capacity = 32_768
        buffer = ctypes.create_unicode_buffer(capacity)
        required = ctypes.c_uint32()
        if not get_paths(volume_name, buffer, capacity, ctypes.byref(required)):
            error_code = ctypes.get_last_error()
            if error_code in {2, 3, 15, 21, 1167}:
                return ()
            raise ctypes.WinError(error_code)
        raw = buffer[: required.value]
        return tuple(Path(value) for value in raw.split("\0") if value)

    def free_bytes(self, path: Path) -> int:
        if os.name != "nt":
            return shutil.disk_usage(path).free
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_space = kernel32.GetDiskFreeSpaceExW
        get_space.argtypes = [
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_uint64),
        ]
        get_space.restype = ctypes.c_int
        available = ctypes.c_uint64()
        total = ctypes.c_uint64()
        free = ctypes.c_uint64()
        if not get_space(
            str(path), ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(available.value)

    @staticmethod
    def _describe_windows(path: Path) -> VolumeDescriptor:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_volume_path = kernel32.GetVolumePathNameW
        get_volume_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        get_volume_path.restype = ctypes.c_int
        mount_buffer = ctypes.create_unicode_buffer(32_768)
        if not get_volume_path(str(path), mount_buffer, len(mount_buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        mount = mount_buffer.value

        get_volume_name = kernel32.GetVolumeNameForVolumeMountPointW
        get_volume_name.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        get_volume_name.restype = ctypes.c_int
        name_buffer = ctypes.create_unicode_buffer(32_768)
        if not get_volume_name(mount, name_buffer, len(name_buffer)):
            raise ctypes.WinError(ctypes.get_last_error())

        get_information = kernel32.GetVolumeInformationW
        get_information.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        get_information.restype = ctypes.c_int
        label_buffer = ctypes.create_unicode_buffer(261)
        filesystem_buffer = ctypes.create_unicode_buffer(261)
        serial = ctypes.c_uint32()
        maximum_component = ctypes.c_uint32()
        flags = ctypes.c_uint32()
        if not get_information(
            mount,
            label_buffer,
            len(label_buffer),
            ctypes.byref(serial),
            ctypes.byref(maximum_component),
            ctypes.byref(flags),
            filesystem_buffer,
            len(filesystem_buffer),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return VolumeDescriptor(
            volume_id=name_buffer.value.rstrip("\\"),
            mount_path=Path(mount),
            serial_number=int(serial.value),
            filesystem=filesystem_buffer.value or None,
            label=label_buffer.value or None,
        )


class VolumeService:
    """Bind configured libraries to volumes and safely resolve reconnections."""

    def __init__(self, backend: VolumeBackend | None = None) -> None:
        self._backend = backend or SystemVolumeBackend()

    def bind(self, path: Path) -> VolumeBinding:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise OSError(f"Configured library is not a folder: {path}")
        volume = self._backend.describe(resolved)
        relative = resolved.relative_to(volume.mount_path.resolve(strict=True))
        return VolumeBinding(
            volume_id=volume.volume_id,
            relative_path=str(relative),
            last_mount_path=volume.mount_path,
            serial_number=volume.serial_number,
            filesystem=volume.filesystem,
            label=volume.label,
        )

    def resolve(self, configured_path: Path, binding: VolumeBinding | None) -> DestinationStatus:
        if binding is None:
            try:
                descriptor = self._backend.describe(configured_path)
                free = self._backend.free_bytes(configured_path)
            except OSError as error:
                return DestinationStatus(
                    False,
                    configured_path,
                    None,
                    None,
                    f"Library unavailable: {error}",
                )
            return DestinationStatus(
                True,
                configured_path.resolve(strict=True),
                descriptor.volume_id,
                free,
                self._available_detail(descriptor, free),
            )

        try:
            discovered_mounts = self._backend.mount_paths(binding.volume_id)
        except OSError as error:
            return DestinationStatus(
                False,
                configured_path,
                binding.volume_id,
                None,
                f"Cannot inspect expected library volume: {error}",
            )
        mounts = (binding.last_mount_path, *discovered_mounts)
        seen: set[str] = set()
        for mount in mounts:
            identity = os.path.normcase(str(mount.resolve(strict=False)))
            if identity in seen:
                continue
            seen.add(identity)
            candidate = mount.joinpath(binding.relative_path)
            try:
                if not candidate.is_dir():
                    continue
                descriptor = self._backend.describe(candidate)
                if descriptor.volume_id.casefold() != binding.volume_id.casefold():
                    continue
                free = self._backend.free_bytes(candidate)
            except OSError:
                continue
            return DestinationStatus(
                True,
                candidate.resolve(strict=True),
                descriptor.volume_id,
                free,
                self._available_detail(descriptor, free),
            )
        name = binding.label or binding.volume_id
        return DestinationStatus(
            False,
            configured_path,
            binding.volume_id,
            None,
            f"Expected library volume is disconnected: {name}",
        )

    def same_volume(self, first: Path, second: Path) -> bool:
        return (
            self._backend.describe(first).volume_id.casefold()
            == self._backend.describe(second).volume_id.casefold()
        )

    def identity(self, path: Path) -> str:
        return self._backend.describe(path).volume_id

    def free_bytes(self, path: Path) -> int:
        return self._backend.free_bytes(path)

    @staticmethod
    def _available_detail(descriptor: VolumeDescriptor, free_bytes: int) -> str:
        name = descriptor.label or descriptor.volume_id
        gib = free_bytes / (1024**3)
        return f"Available on {name} - {gib:.1f} GB free"
