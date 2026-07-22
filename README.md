# JWDM

Jeebus' Windows Download Manager is a Windows-first desktop utility for safe,
transparent organization of downloaded files.

## Current scope

The repository is currently at **Phase 0**: a maintainable Python package,
minimal PySide6 window and tray shell, structured local logging, automated smoke
tests, and a tracked PyInstaller build. File scanning, watching, classification,
and moving are intentionally not implemented yet.

## Requirements

- Windows 10 or Windows 11, 64-bit
- Python 3.12, 64-bit
- PowerShell 5.1 or newer

## Build and run

From PowerShell in the repository root:

```powershell
.\Build.ps1
```

The script creates or updates `.venv`, installs pinned dependencies, runs the
smoke tests, builds `dist\test\JWDM\JWDM.exe`, launches it, and verifies that the
compiled process remains running.

Optional switches:

```powershell
.\Build.ps1 -Clean
.\Build.ps1 -NoLaunch
.\Build.ps1 -Release
```

`-Release` currently produces a one-file packaging check at
`dist\release\JWDM.exe`; release signing and installer work remain deferred.

## Run tests directly

After the first build has created the environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Runtime logs use JSON Lines format at `%LOCALAPPDATA%\JWDM\logs\jwdm.log.jsonl`.
