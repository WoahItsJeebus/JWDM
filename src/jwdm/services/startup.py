"""Per-user Windows startup registration without administrator privileges."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol


class StartupError(RuntimeError):
    """The current user's startup entry could not be inspected or changed."""


class StartupBackend(Protocol):
    def read(self, name: str) -> str | None: ...

    def write(self, name: str, command: str) -> None: ...

    def delete(self, name: str) -> None: ...


class WindowsRunKeyBackend:
    """Store one command under the current user's standard Run key."""

    _SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def read(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._SUBKEY) as key:
                value, value_type = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise StartupError(f"Cannot inspect the Windows startup entry: {error}") from error
        if value_type != winreg.REG_SZ or not isinstance(value, str):
            raise StartupError("The existing JWDM startup entry has an unexpected value type.")
        return value

    def write(self, name: str, command: str) -> None:
        import winreg

        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                self._SUBKEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        except OSError as error:
            raise StartupError(f"Cannot enable Start with Windows: {error}") from error

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self._SUBKEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return
        except OSError as error:
            raise StartupError(f"Cannot disable Start with Windows: {error}") from error


class StartupManager:
    """Create, repair, or remove JWDM's single per-user startup command."""

    ENTRY_NAME = "JWDM"

    def __init__(
        self,
        executable: Path,
        backend: StartupBackend | None = None,
        *,
        module_mode: bool = False,
    ) -> None:
        self._executable = executable.resolve(strict=False)
        self._backend = backend or WindowsRunKeyBackend()
        self._module_mode = module_mode

    @classmethod
    def for_current_process(cls) -> StartupManager:
        frozen = bool(getattr(sys, "frozen", False))
        return cls(Path(sys.executable), module_mode=not frozen)

    def expected_command(self, launch_minimized: bool) -> str:
        arguments = [str(self._executable)]
        if self._module_mode:
            arguments.extend(("-m", "jwdm.main"))
        if launch_minimized:
            arguments.append("--minimized")
        return subprocess.list2cmdline(arguments)

    def is_current(self, launch_minimized: bool) -> bool:
        return self._backend.read(self.ENTRY_NAME) == self.expected_command(
            launch_minimized
        )

    def synchronize(self, enabled: bool, launch_minimized: bool) -> None:
        if enabled:
            expected = self.expected_command(launch_minimized)
            if self._backend.read(self.ENTRY_NAME) != expected:
                self._backend.write(self.ENTRY_NAME, expected)
            return
        self._backend.delete(self.ENTRY_NAME)
