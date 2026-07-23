# JWDM

Jeebus' Windows Download Manager is a Windows-first desktop utility for safe,
transparent organization of downloaded files.

## Current scope

JWDM has completed **Phase 6** and the planned 1.0 feature phases. It includes
the manual and automatic organization workflows, rules and settings, resilient
external-library handling, bounded offline smart classification, reversible
Windows Downloads relocation, and release packaging.

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

1. Choose an existing organized library and one or more incoming folders.
2. Select **Start automatic organization**. Monitoring is top-level only. A
   setting can opt into processing files already present when monitoring starts.
3. New candidates must remain unchanged for four samples and at least three
   quiet seconds, then pass an exclusive Windows read-access probe.
4. Explicit user rules run first. High-confidence archive, image, texture, and
   extension results move through the same journaled transaction and undo history.
   Unknown, suspicious, or low-confidence results remain in **Needs review**
   unless the optional `Unknown` destination is enabled for unmatched formats.
5. Pause or resume processing from either the main window or tray. Pending paths
   survive restart, but readiness sampling restarts from zero for safety.
6. If the bound library volume disconnects, candidates remain queued at their
   sources. Processing resumes only when the same volume identity reconnects,
   including at a different drive letter.

Double-click an automatic candidate for detailed review. Double-clicking a
`No built-in rule...` detail opens a prefilled **Rules > Add** editor. Completed
moves leave the active candidate list and remain available in History.

Top-level folders route intact to `Folders` through the same journaled,
verified, undoable move service. Recursive manual scans continue to organize the
files inside a folder rather than moving that selected tree as one item.

## Rules and settings

- **Rules** supports enabled extension rules that route to a validated category,
  require review, or ignore matching files. User rules run before built-in rules.
- Manual category corrections offer an unchecked rule suggestion for the file's
  extension. Confirmed suggestions create or update rules atomically before any
  approved files move; conflicting suggestions are refused.
- **Settings** controls Start with Windows, launch minimized, close-to-tray,
  automatic startup, multiple incoming folders, existing-file catch-up,
  confidence policy, the optional `Unknown` route, and excluded folders.
- Start with Windows uses the current user's standard Windows Run entry and does
  not require administrator privileges.
- Closing the window minimizes to the tray by default when a tray is available;
  the tray's **Exit** command always stops JWDM.

## Windows Downloads relocation

The **Windows Downloads** tab in Settings can redirect the current user's
Windows Downloads known folder to an existing local folder and later restore the
recorded original location. JWDM uses the supported Windows Known Folder API,
records a recovery checkpoint before changing Windows, verifies the resulting
path, and does not edit the registry directly.

Relocation never copies, merges, moves, or deletes files already in either
folder. Existing files can be organized separately through the normal preview
workflow. Relocation and restore are disabled while automatic organization or a
manual scan is active, and unsafe overlaps, network paths, drive roots,
symlinks, and junctions are refused.

## Requirements

- Windows 10 or Windows 11, 64-bit
- Python 3.12, 64-bit
- PowerShell 5.1 or newer
- Inno Setup 6 or 7 only for release-installer builds

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

`-Release` produces an installed-layout onedir application at
`dist\release\JWDM\JWDM.exe`, a per-user installer at
`dist\installer\JWDM-Setup-1.0.0-x64.exe`, and SHA-256 checksums. Use
`-Release -SkipInstaller` only for an application-only packaging check when Inno
Setup is unavailable.

Official releases must use `-RequireSignature` with the project owner's
Authenticode certificate. Local unsigned release builds are permitted for
packaging validation but are not publishable releases. The complete signing,
installer, and update policy is in [docs/RELEASE.md](docs/RELEASE.md).

## Run tests directly

After the first build has created the environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Runtime logs use JSON Lines format at `%LOCALAPPDATA%\JWDM\logs\jwdm.log.jsonl`.
The append-only move/undo and recovery journal is stored at
`%LOCALAPPDATA%\JWDM\history.jsonl`. Settings, rules, exclusions, pending
candidate paths, organized-library volume binding, and Downloads restore record
are stored separately in `%LOCALAPPDATA%\JWDM\state.db`.
