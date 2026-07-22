from __future__ import annotations

import ctypes
import os
from pathlib import Path

import pytest

from jwdm.pipeline.result import StageOutcome
from jwdm.pipeline.stages.access import WindowsAccessProbe


def test_access_probe_passes_for_unlocked_file(tmp_path: Path) -> None:
    path = tmp_path / "available.pdf"
    path.write_bytes(b"content")

    assert WindowsAccessProbe().probe(path).outcome is StageOutcome.PASS


@pytest.mark.skipif(os.name != "nt", reason="Win32 sharing semantics are Windows-only")
def test_access_probe_defers_exclusively_open_file(tmp_path: Path) -> None:
    from ctypes import wintypes

    path = tmp_path / "locked.pdf"
    path.write_bytes(b"content")
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
    handle = create_file(str(path), 0x80000000, 0, None, 3, 0x0080, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        result = WindowsAccessProbe().probe(path)
    finally:
        close_handle(handle)

    assert result.outcome is StageOutcome.DEFER
    assert result.error_code == 32
