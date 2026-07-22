"""Conservative Windows exclusive-access probe."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Final, Protocol

from jwdm.pipeline.result import StageOutcome, StageResult

_GENERIC_READ: Final = 0x80000000
_OPEN_EXISTING: Final = 3
_FILE_ATTRIBUTE_NORMAL: Final = 0x0080
_INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value


class AccessProbe(Protocol):
    def probe(self, path: Path) -> StageResult: ...


class WindowsAccessProbe:
    """Attempt a read handle with no sharing permitted."""

    def probe(self, path: Path) -> StageResult:
        if os.name != "nt":
            return self._portable_probe(path)

        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            self._long_windows_path(path),
            _GENERIC_READ,
            0,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            error_code = ctypes.get_last_error()
            return StageResult(
                StageOutcome.DEFER,
                f"Exclusive file access is not available (Windows error {error_code})",
                error_code,
            )
        if not close_handle(handle):
            error_code = ctypes.get_last_error()
            return StageResult(
                StageOutcome.DEFER,
                f"Access probe handle could not be closed (Windows error {error_code})",
                error_code,
            )
        return StageResult(StageOutcome.PASS, "Exclusive read access succeeded")

    @staticmethod
    def _long_windows_path(path: Path) -> str:
        absolute = str(path.resolve(strict=False))
        if absolute.startswith("\\\\?\\"):
            return absolute
        if absolute.startswith("\\\\"):
            return "\\\\?\\UNC\\" + absolute[2:]
        return "\\\\?\\" + absolute

    @staticmethod
    def _portable_probe(path: Path) -> StageResult:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError as error:
            return StageResult(StageOutcome.DEFER, f"File access failed: {error}", error.errno)
        os.close(descriptor)
        return StageResult(StageOutcome.PASS, "File can be opened")
