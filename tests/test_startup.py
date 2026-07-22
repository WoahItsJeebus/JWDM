from __future__ import annotations

from pathlib import Path

from jwdm.services.startup import StartupManager


class _Backend:
    def __init__(self) -> None:
        self.value: str | None = None
        self.writes = 0
        self.deletes = 0

    def read(self, name: str) -> str | None:
        assert name == "JWDM"
        return self.value

    def write(self, name: str, command: str) -> None:
        assert name == "JWDM"
        self.value = command
        self.writes += 1

    def delete(self, name: str) -> None:
        assert name == "JWDM"
        self.value = None
        self.deletes += 1


def test_startup_manager_repairs_stale_entry_without_duplicates() -> None:
    backend = _Backend()
    executable = Path(r"C:\Program Files\JWDM\JWDM.exe")
    manager = StartupManager(executable, backend)

    manager.synchronize(True, True)
    first_command = backend.value
    manager.synchronize(True, True)

    assert first_command is not None
    assert "JWDM.exe" in first_command
    assert "--minimized" in first_command
    assert backend.writes == 1

    backend.value = r'"D:\Old\JWDM.exe" --minimized'
    manager.synchronize(True, False)
    assert backend.writes == 2
    assert backend.value == manager.expected_command(False)
    manager.synchronize(False, False)
    assert backend.value is None
    assert backend.deletes == 1
