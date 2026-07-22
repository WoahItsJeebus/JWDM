# JWDM

Jeebus' Windows Download Manager is a Windows-first desktop utility for safe,
transparent organization of downloaded files.

## Current scope

The repository is currently at **Phase 5**. JWDM includes the complete manual,
automatic, rules, settings, and external-library workflows plus bounded ZIP
inspection, raster metadata classification, texture filename signals, and
explicit review corrections that can become durable user rules.

Windows Downloads relocation, restore, installer/signing, and update work remain
intentionally deferred to Phase 6.

## Manual organization

1. Choose an existing organized-library folder.
2. Select one or more source folders and decide whether each includes subfolders.
3. Follow the scan status from discovery through determinate file analysis.
4. Review every proposed source, category, destination, reason, and size. The
   preview fits its columns to the active display and keeps them manually resizable.
5. Assign a category to unknown or review-required items. JWDM can suggest an
   extension rule from that correction, but saves it only when explicitly selected.
6. Approve the files to move and confirm execution.
7. Use **History** to inspect records or **Undo last move** to restore the latest
   unchanged file when its original path is still free.

Selected source folders are never registered for automatic monitoring. Managed
library subfolders are excluded from organize-in-place scans. Cross-volume moves
copy to an application-owned temporary file, verify SHA-256 content, publish the
destination, and only then remove the unchanged source.

## Automatic organization

1. Choose an existing organized library and one incoming folder.
2. Select **Start automatic organization**. Monitoring is top-level only. A
   setting can opt into processing files already present when monitoring starts.
3. New candidates must remain unchanged for four samples and at least three
   quiet seconds, then pass an exclusive Windows read-access probe.
4. Explicit user rules run first. High-confidence archive, image, texture, and
   extension results move through the same journaled transaction and undo history.
   Unknown, suspicious, or low-confidence results remain in **Needs review**.
5. Pause or resume processing from either the main window or tray. Pending paths
   survive restart, but readiness sampling restarts from zero for safety.
6. If the bound library volume disconnects, candidates remain queued at their
   sources. Processing resumes only when the same volume identity reconnects,
   including at a different drive letter.

## Rules and settings

- **Rules** supports enabled extension rules that route to a validated category,
  require review, or ignore matching files. User rules run before built-in rules.
- Manual category corrections offer an unchecked rule suggestion for the file's
  extension. Confirmed suggestions create or update rules atomically before any
  approved files move; conflicting suggestions are refused.
- **Settings** controls Start with Windows, launch minimized, close-to-tray,
  automatic startup, existing-file catch-up, confidence policy, and excluded
  folders.
- Start with Windows uses the current user's standard Windows Run entry and does
  not require administrator privileges.
- Closing the window minimizes to the tray by default when a tray is available;
  the tray's **Exit** command always stops JWDM.

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
The append-only move/undo and recovery journal is stored at
`%LOCALAPPDATA%\JWDM\history.jsonl`. Settings, rules, exclusions, pending
candidate paths, and the organized-library volume binding are stored separately
in `%LOCALAPPDATA%\JWDM\state.db`.
