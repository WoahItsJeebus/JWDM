"""Crash-recoverable Windows Downloads known-folder relocation and restore."""

from __future__ import annotations

import ctypes
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from jwdm.config import DownloadsRelocationRecord, DownloadsRelocationState
from jwdm.logging_config import APPLICATION_LOGGER
from jwdm.persistence.state import StateError, StateRepository


class DownloadsRelocationError(RuntimeError):
    """A Downloads redirection was unsafe, unsupported, or could not be verified."""


@dataclass(frozen=True, slots=True)
class DownloadsStatus:
    supported: bool
    current_path: Path | None
    record: DownloadsRelocationRecord | None
    can_relocate: bool
    can_restore: bool
    detail: str


class KnownFolderBackend(Protocol):
    def current_downloads(self) -> Path: ...

    def redirect_downloads(self, target: Path) -> None: ...


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


_DOWNLOADS_FOLDER_ID = _GUID.from_buffer_copy(
    uuid.UUID("374de290-123f-4565-9164-39c4925e467b").bytes_le
)


class SystemKnownFolderBackend:
    """Call the per-user Windows Known Folder API without editing the registry."""

    def current_downloads(self) -> Path:
        self._require_windows()
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        get_path = shell32.SHGetKnownFolderPath
        get_path.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        get_path.restype = ctypes.c_long
        allocated = ctypes.c_wchar_p()
        result = get_path(
            ctypes.byref(_DOWNLOADS_FOLDER_ID),
            0,
            None,
            ctypes.byref(allocated),
        )
        self._check_hresult(result, "read the Windows Downloads location")
        try:
            if not allocated.value:
                raise OSError("Windows returned an empty Downloads known-folder path.")
            return Path(allocated.value)
        finally:
            ole32 = ctypes.WinDLL("ole32", use_last_error=True)
            free_memory = ole32.CoTaskMemFree
            free_memory.argtypes = [ctypes.c_void_p]
            free_memory.restype = None
            free_memory(ctypes.cast(allocated, ctypes.c_void_p))

    def redirect_downloads(self, target: Path) -> None:
        self._require_windows()
        target_text = str(target)
        if not target_text or len(target_text) >= 260:
            raise OSError(
                "The Windows Downloads known-folder API requires a non-empty path "
                "shorter than 260 characters."
            )
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        set_path = shell32.SHSetKnownFolderPath
        set_path.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_wchar_p,
        ]
        set_path.restype = ctypes.c_long
        result = set_path(
            ctypes.byref(_DOWNLOADS_FOLDER_ID),
            0,
            None,
            target_text,
        )
        self._check_hresult(result, "redirect the Windows Downloads location")

    @staticmethod
    def _require_windows() -> None:
        if os.name != "nt":
            raise OSError("Windows Downloads relocation is available only on Windows.")

    @staticmethod
    def _check_hresult(result: int, action: str) -> None:
        signed = ctypes.c_long(result).value
        if signed < 0:
            code = signed & 0xFFFFFFFF
            raise OSError(f"Windows could not {action} (HRESULT 0x{code:08X}).")


def _identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path.resolve(strict=False))))


def _same_path(first: Path, second: Path) -> bool:
    return _identity(first) == _identity(second)


def _contains(parent: Path, candidate: Path) -> bool:
    parent_identity = _identity(parent)
    candidate_identity = _identity(candidate)
    try:
        return os.path.commonpath((parent_identity, candidate_identity)) == parent_identity
    except ValueError:
        return False


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


class DownloadsRelocationService:
    """Redirect Downloads only after persisting an exact, recoverable restore point."""

    def __init__(
        self,
        repository: StateRepository,
        backend: KnownFolderBackend | None = None,
    ) -> None:
        self._repository = repository
        self._backend = backend or SystemKnownFolderBackend()
        self._system_backend = backend is None
        self._logger = logging.getLogger(f"{APPLICATION_LOGGER}.downloads")

    def status(self) -> DownloadsStatus:
        if self._system_backend and os.name != "nt":
            return DownloadsStatus(
                False,
                None,
                self._repository.downloads_relocation(),
                False,
                False,
                "Windows Downloads relocation is available only on Windows.",
            )
        try:
            current = self._backend.current_downloads().resolve(strict=False)
        except OSError as error:
            raise DownloadsRelocationError(
                f"Cannot inspect the Windows Downloads location: {error}"
            ) from error
        record = self._reconcile(current, self._repository.downloads_relocation())
        state = record.state if record is not None else None
        can_restore = bool(
            record is not None
            and state is DownloadsRelocationState.ACTIVE
            and _same_path(current, record.relocated_path)
        )
        can_relocate = state not in {
            DownloadsRelocationState.PREPARED,
            DownloadsRelocationState.ACTIVE,
            DownloadsRelocationState.RESTORE_PREPARED,
            DownloadsRelocationState.RECOVERY_REQUIRED,
        }
        if record is None:
            detail = "No JWDM Downloads relocation is recorded."
        elif state is DownloadsRelocationState.ACTIVE:
            detail = f"JWDM can restore Downloads to {record.original_path}."
        elif state is DownloadsRelocationState.RESTORED:
            detail = f"The last JWDM relocation was restored to {record.original_path}."
        elif state is DownloadsRelocationState.ROLLED_BACK:
            detail = "The last relocation attempt left the original location unchanged."
        else:
            detail = (
                "The recorded relocation does not match the Windows Downloads path. "
                "No automatic change will be attempted."
            )
        if record is not None and record.error:
            detail = f"{detail} Last error: {record.error}"
        return DownloadsStatus(True, current, record, can_relocate, can_restore, detail)

    def relocate(
        self,
        target: Path,
        *,
        library_path: Path | None = None,
    ) -> DownloadsStatus:
        status = self.status()
        if not status.supported or status.current_path is None:
            raise DownloadsRelocationError(status.detail)
        if not status.can_relocate:
            raise DownloadsRelocationError(
                "Restore or resolve the existing Downloads relocation before starting another."
            )
        current = self._validate_folder(status.current_path, "Current Downloads")
        relocated = self._validate_folder(target, "New Downloads")
        self._validate_relationships(current, relocated, library_path)
        self._probe_writable(relocated)

        now = datetime.now(UTC)
        record = DownloadsRelocationRecord(
            original_path=current,
            relocated_path=relocated,
            state=DownloadsRelocationState.PREPARED,
            created_at=now,
            updated_at=now,
        )
        self._repository.save_downloads_relocation(record)
        self._logger.info(
            "Downloads relocation prepared",
            extra={
                "event": "downloads_relocation_prepared",
                "source": str(current),
                "destination": str(relocated),
            },
        )
        try:
            self._backend.redirect_downloads(relocated)
            observed = self._backend.current_downloads().resolve(strict=False)
            if not _same_path(observed, relocated):
                raise OSError(
                    f"Windows reports {observed} instead of the requested {relocated}."
                )
        except OSError as error:
            self._record_failed_attempt(record, error, restoring=False)
            self._logger.error(
                "Downloads relocation failed",
                extra={"event": "downloads_relocation_failed"},
                exc_info=True,
            )
            raise DownloadsRelocationError(f"Downloads was not safely relocated: {error}") from error

        self._repository.save_downloads_relocation(
            replace(
                record,
                state=DownloadsRelocationState.ACTIVE,
                updated_at=datetime.now(UTC),
            )
        )
        self._logger.info(
            "Downloads relocation completed",
            extra={
                "event": "downloads_relocation_completed",
                "source": str(current),
                "destination": str(relocated),
            },
        )
        return self.status()

    def restore(self) -> DownloadsStatus:
        status = self.status()
        record = status.record
        if not status.can_restore or record is None or status.current_path is None:
            raise DownloadsRelocationError(
                "The current Downloads location does not match an active JWDM restore point."
            )
        original = self._validate_folder(record.original_path, "Recorded original Downloads")
        if not _same_path(status.current_path, record.relocated_path):
            raise DownloadsRelocationError(
                "Windows Downloads changed outside JWDM; restore was refused."
            )
        prepared = replace(
            record,
            state=DownloadsRelocationState.RESTORE_PREPARED,
            updated_at=datetime.now(UTC),
            error=None,
        )
        self._repository.save_downloads_relocation(prepared)
        self._logger.info(
            "Downloads restore prepared",
            extra={
                "event": "downloads_restore_prepared",
                "source": str(record.relocated_path),
                "destination": str(original),
            },
        )
        try:
            self._backend.redirect_downloads(original)
            observed = self._backend.current_downloads().resolve(strict=False)
            if not _same_path(observed, original):
                raise OSError(
                    f"Windows reports {observed} instead of the recorded {original}."
                )
        except OSError as error:
            self._record_failed_attempt(prepared, error, restoring=True)
            self._logger.error(
                "Downloads restore failed",
                extra={"event": "downloads_restore_failed"},
                exc_info=True,
            )
            raise DownloadsRelocationError(f"Downloads was not safely restored: {error}") from error

        self._repository.save_downloads_relocation(
            replace(
                prepared,
                state=DownloadsRelocationState.RESTORED,
                updated_at=datetime.now(UTC),
            )
        )
        self._logger.info(
            "Downloads restore completed",
            extra={
                "event": "downloads_restore_completed",
                "source": str(record.relocated_path),
                "destination": str(original),
            },
        )
        return self.status()

    def _reconcile(
        self,
        current: Path,
        record: DownloadsRelocationRecord | None,
    ) -> DownloadsRelocationRecord | None:
        if record is None:
            return None
        state = record.state
        reconciled = state
        if state in {
            DownloadsRelocationState.PREPARED,
            DownloadsRelocationState.RECOVERY_REQUIRED,
        }:
            if _same_path(current, record.relocated_path):
                reconciled = DownloadsRelocationState.ACTIVE
            elif _same_path(current, record.original_path):
                reconciled = DownloadsRelocationState.ROLLED_BACK
            else:
                reconciled = DownloadsRelocationState.RECOVERY_REQUIRED
        elif state is DownloadsRelocationState.RESTORE_PREPARED:
            if _same_path(current, record.original_path):
                reconciled = DownloadsRelocationState.RESTORED
            elif _same_path(current, record.relocated_path):
                reconciled = DownloadsRelocationState.ACTIVE
            else:
                reconciled = DownloadsRelocationState.RECOVERY_REQUIRED
        elif state is DownloadsRelocationState.ACTIVE:
            if _same_path(current, record.original_path):
                reconciled = DownloadsRelocationState.RESTORED
            elif not _same_path(current, record.relocated_path):
                reconciled = DownloadsRelocationState.RECOVERY_REQUIRED
        if reconciled is state:
            return record
        updated = replace(record, state=reconciled, updated_at=datetime.now(UTC))
        self._repository.save_downloads_relocation(updated)
        return updated

    def _record_failed_attempt(
        self,
        record: DownloadsRelocationRecord,
        error: OSError,
        *,
        restoring: bool,
    ) -> None:
        try:
            observed = self._backend.current_downloads().resolve(strict=False)
        except OSError as inspection_error:
            state = DownloadsRelocationState.RECOVERY_REQUIRED
            detail = f"{error}; current location could not be verified: {inspection_error}"
        else:
            detail = str(error)
            if _same_path(observed, record.original_path):
                state = (
                    DownloadsRelocationState.RESTORED
                    if restoring
                    else DownloadsRelocationState.ROLLED_BACK
                )
            elif _same_path(observed, record.relocated_path):
                state = DownloadsRelocationState.ACTIVE
            else:
                state = DownloadsRelocationState.RECOVERY_REQUIRED
        self._repository.save_downloads_relocation(
            replace(
                record,
                state=state,
                updated_at=datetime.now(UTC),
                error=detail,
            )
        )

    @staticmethod
    def _validate_folder(path: Path, label: str) -> Path:
        expanded = path.expanduser()
        if str(expanded).startswith("\\\\"):
            raise DownloadsRelocationError(
                f"{label} cannot be a network path: {path}"
            )
        if not expanded.exists() or not expanded.is_dir():
            raise DownloadsRelocationError(f"{label} must be an existing folder: {path}")
        if _is_link_or_junction(expanded):
            raise DownloadsRelocationError(
                f"{label} cannot be a symbolic link or junction: {path}"
            )
        try:
            resolved = expanded.resolve(strict=True)
        except OSError as error:
            raise DownloadsRelocationError(f"Cannot resolve {label}: {error}") from error
        if resolved == Path(resolved.anchor):
            raise DownloadsRelocationError(f"{label} cannot be a drive root: {resolved}")
        if len(str(resolved)) >= 260:
            raise DownloadsRelocationError(
                f"{label} must be shorter than 260 characters for the Windows API."
            )
        return resolved

    @staticmethod
    def _validate_relationships(
        current: Path,
        target: Path,
        library_path: Path | None,
    ) -> None:
        if _same_path(current, target):
            raise DownloadsRelocationError(
                "The proposed Downloads location is already active."
            )
        if _contains(current, target) or _contains(target, current):
            raise DownloadsRelocationError(
                "The current and proposed Downloads folders cannot contain one another."
            )
        if library_path is None:
            return
        library = library_path.expanduser().resolve(strict=False)
        if _contains(library, target) or _contains(target, library):
            raise DownloadsRelocationError(
                "The proposed Downloads folder and organized library cannot overlap."
            )

    @staticmethod
    def _probe_writable(path: Path) -> None:
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".jwdm-write-probe-",
                dir=path,
                delete=True,
            ):
                pass
        except OSError as error:
            raise DownloadsRelocationError(
                f"The proposed Downloads folder is not writable: {error}"
            ) from error
