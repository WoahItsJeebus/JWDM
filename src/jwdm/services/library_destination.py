"""Persist and resolve the organized library across removable-volume changes."""

from __future__ import annotations

import threading
from pathlib import Path

from jwdm.config import VolumeBinding
from jwdm.persistence.state import StateRepository
from jwdm.services.volumes import DestinationStatus, VolumeService


class LibraryDestinationService:
    """Bind the configured library to a volume identity and resolve its live path."""

    ROLE = "library"

    def __init__(
        self,
        repository: StateRepository,
        volumes: VolumeService | None = None,
    ) -> None:
        self._repository = repository
        self._volumes = volumes or VolumeService()
        self._binding = repository.volume_binding(self.ROLE)
        self._lock = threading.RLock()

    @property
    def volumes(self) -> VolumeService:
        return self._volumes

    def configure(self, path: Path) -> VolumeBinding:
        binding = self._volumes.bind(path)
        self._repository.save_volume_binding(self.ROLE, binding)
        with self._lock:
            self._binding = binding
        return binding

    def ensure_binding(self, path: Path | None) -> None:
        if path is None:
            return
        with self._lock:
            binding = self._binding
        if binding is None and path.is_dir():
            self.configure(path)

    def status(self, configured_path: Path) -> DestinationStatus:
        with self._lock:
            binding = self._binding
        status = self._volumes.resolve(configured_path, binding)
        binding_mount_matches = (
            binding is not None
            and status.path.resolve(strict=False).is_relative_to(
                binding.last_mount_path.resolve(strict=False)
            )
        )
        if status.available and (binding is None or not binding_mount_matches):
            refreshed = self._volumes.bind(status.path)
            if refreshed != binding:
                self._repository.save_volume_binding(self.ROLE, refreshed)
                with self._lock:
                    self._binding = refreshed
        return status
