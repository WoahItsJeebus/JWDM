# JWDM Project Context

## 1. Project Identity

**Name:** JWDM  
**Expanded name:** Jeebus' Windows Download Manager  
**Repository:** `https://github.com/WoahItsJeebus/JWDM`  
**Primary platform:** Windows 10 and Windows 11, 64-bit  
**Primary language:** Python  
**Application type:** Native-feeling Windows desktop utility  
**Primary owner:** WoahItsJeebus

JWDM is a Windows download and file-intake organizer. It watches one or more configured incoming folders, waits until files are completely written and safe to touch, classifies them through a modular pipeline, and moves them into user-configurable category folders. It also provides a manual one-click **Organize** workflow for existing folders and an optional hands-free automatic mode.

The project should feel trustworthy, transparent, reversible, and useful to ordinary users who do not write code.

## 2. Product Vision

Windows Downloads folders tend to become long-lived junk drawers. Existing sorting scripts are often too simple, opaque, destructive, or difficult to configure.

JWDM should provide:

- A single **Organize** button for selecting one or more folders to organize on demand.
- A hands-free **Automatic Organization** mode for configured incoming folders.
- Safe detection of files that are still downloading or otherwise being written.
- A modular classification pipeline whose reasoning can be inspected and extended.
- Configurable categories and rules.
- Optional relocation of the Windows Downloads known folder to another drive.
- Support for a separate organized library, including external drives.
- Preview, audit history, undo, and cautious collision handling.
- A one-click PowerShell build that produces and launches a compiled executable.

The central promise is:

> JWDM organizes incoming files without grabbing unfinished downloads, hiding its decisions, overwriting data, or trapping the user in its folder structure.

## 3. Product Principles

### 3.1 Safety before cleverness

A false negative is preferable to a destructive false positive. If JWDM is uncertain whether a file is complete, writable, classifiable, or movable, it should defer or request review.

### 3.2 Explain every decision

Every proposed or completed move should expose:

- Source path
- Destination path
- Category
- Confidence or certainty
- Matching rule or classification signal
- Timestamp
- Collision behavior
- Final status

### 3.3 Every move is reversible

Every successful move must create an undo record. JWDM must never silently overwrite an existing file.

### 3.4 Manual control and automation are peers

The manual **Organize** workflow is not a lesser fallback. Some users will only use JWDM manually. Automatic organization is optional and independently configurable.

### 3.5 Modular stages, not one giant classifier

Each processing stage should live in its own module and return an explicit result. The pipeline must remain testable and extensible.

### 3.6 Compiled-executable-first testing

Development should be routinely tested through the compiled Windows executable, not only by running Python files directly. The executable experience is part of development, not a final packaging chore.

## 4. Core User Workflows

### 4.1 Manual Organize workflow

The main window contains a prominent **Organize** button.

When pressed:

1. Open a Windows folder picker that supports selecting one or more folders.
2. Ask whether each selected folder should be scanned at the top level only or recursively.
3. Validate selected source and destination relationships.
4. Scan without moving anything.
5. Build a reviewable organization plan.
6. Show files by proposed category, files needing review, unfinished or locked files, excluded files, collisions, total count, and estimated bytes.
7. Let the user approve all safe moves, approve selected moves, change destinations, create a rule from a correction, exclude items, or cancel.
8. Execute approved moves transactionally.
9. Record history and undo data.

A selected folder is **not** automatically added as a watched incoming folder unless the user explicitly chooses that option.

Manual scans may organize any user-selected folder, not only the Windows Downloads folder.

### 4.2 Automatic Organization workflow

Automatic mode watches one or more configured incoming folders.

For each newly created or moved-in file:

1. Register or update one candidate entry.
2. Wait for the readiness pipeline to prove it is safe to process.
3. Classify it.
4. Resolve its destination.
5. Apply the configured confidence policy: move automatically, ask before moving, or add to review.
6. Move safely.
7. Record history and undo information.
8. Optionally show a Windows notification.

Automatic mode must be pausable from both the main window and tray menu.

### 4.3 Existing Downloads scan

On first run, JWDM may offer to scan files already present in the configured Downloads or incoming folder.

Options:

- Review and organize existing files
- Start with new files only
- Do this later

The initial scan must generate a preview before moving anything. Top-level scanning is the default. Recursive scanning is optional. Files that appear unfinished enter a pending state rather than being permanently skipped.

### 4.4 Windows Downloads relocation

JWDM may optionally relocate the Windows Downloads known folder to another location, such as an external drive.

Recommended layout:

```text
E:\JWDM\
├── Incoming\
└── Library\
    ├── Blender\
    ├── Images\
    ├── Documents\
    ├── Installers\
    └── Archives\
```

Two concepts must remain separate:

- **Incoming folder:** where files first appear.
- **Organized library:** where completed, classified files are placed.

Relocation must be optional. Requirements:

- Show the current Downloads path and proposed new path.
- Preserve enough information to restore the previous Downloads location.
- Clearly state that some applications use their own download path.
- Prefer supported Windows shell/known-folder behavior.
- Do not casually edit registry values without validation, backup, and shell notification.
- Avoid administrator privileges unless genuinely unavoidable.
- Refuse unsafe source/destination relationships.
- Do not relocate existing files without preview and confirmation.

## 5. Folder Models and Path Rules

### 5.1 Separate library mode, recommended

Example:

```text
Incoming: E:\JWDM\Incoming
Library:  E:\JWDM\Library
```

Rules:

- Incoming and library paths may not be identical.
- Neither path may contain the other.
- Reject overlap through symbolic links, junctions, mount points, or equivalent aliases where practical.
- Watch incoming roots, not managed library folders.
- External-library unavailability pauses processing rather than triggering fallback behavior.

### 5.2 Organize-in-place mode

Example:

```text
Incoming: C:\Users\User\Downloads
Managed destination: C:\Users\User\Downloads\Sorted
```

Rules:

- Automatic monitoring is nonrecursive by default.
- Managed folders are explicitly tracked and excluded.
- Manual scans exclude managed folders unless deliberately overridden.
- JWDM must not process files created by its own move operation as fresh incoming files.
- Internal operation IDs and candidate suppression should supplement path exclusions.

### 5.3 Destination availability

Before a move:

- Confirm destination exists or can be created.
- Confirm destination is writable.
- Confirm adequate free space.
- Confirm source and destination have not become the same path.
- If an external drive is unavailable, leave files safely in place and queue them.
- Never silently fall back to another destination.
- Track removable destinations by volume identity as well as drive letter where feasible.

## 6. Download and File Readiness

JWDM cannot assume every new filesystem event represents a finished browser download. A candidate may have been downloaded, copied, moved, extracted, created, or synced.

Core rule:

> Any newly created or moved-in file in a configured incoming folder may become a candidate, but nothing is classified or moved until readiness passes.

### 6.1 Temporary and partial files

Recognize configurable suffixes and patterns including:

```text
.crdownload
.part
.partial
.download
.tmp
Unconfirmed *.crdownload
```

This is not the only completion signal.

### 6.2 Stability window

Track at minimum:

- File size
- Last modified timestamp
- Last observed filesystem event
- First seen timestamp
- Current candidate state

A candidate must remain unchanged across a configurable quiet period and sample count.

Initial defaults may be approximately:

- Sample interval: 750 ms
- Required stable samples: 4
- Minimum quiet period: 3 seconds

Any size or modification change resets readiness timing.

### 6.3 Access and lock probe

Use a conservative Windows file-access probe before moving. A Win32 implementation may use `CreateFileW` with restrictive sharing, but must handle access-denied, antivirus scanning, permission errors, and transient locks.

A failed probe normally yields `DEFER`, not `FAIL`. It cannot prove completion alone and must be combined with the stability window.

### 6.4 Rename signals

A rename from a temporary filename to a final-looking filename is a strong signal, not proof. Normal readiness checks still apply.

### 6.5 Format-aware validation

Later stages may non-destructively validate understood formats, such as ZIP indexes, image headers, JSON, PDFs, or PE executables. Unknown formats must not be deleted or branded corrupt merely because JWDM cannot inspect them.

## 7. Candidate Registry

Filesystem streams are noisy. Maintain one active candidate per normalized source identity/path.

Conceptual model:

```python
CandidateContext(
    candidate_id=...,
    source_path=...,
    incoming_root=...,
    first_seen_at=...,
    last_event_at=...,
    last_size=...,
    last_modified_at=...,
    state=...,
    signals=[],
    proposed_category=None,
    proposed_destination=None,
    confidence=None,
    retry_count=0,
)
```

Possible visible states:

```text
Detected
Downloading
Still changing
Waiting for file access
Cooling down
Ready
Classifying
Needs review
Queued for destination
Moving
Moved
Deferred
Failed
```

The registry must deduplicate events, associate renames where possible, back off on locked files, preserve meaningful pending operations across restarts, suppress the app's own moves, and support cancellation/retry.

## 8. Processing Pipeline

Each stage returns:

```text
PASS
DEFER
REJECT
REVIEW
FAIL
```

Suggested sequence:

```text
Filesystem event or manual scan
    ↓
Candidate registration
    ↓
Path and exclusion validation
    ↓
Temporary-file detection
    ↓
Readiness and stability gate
    ↓
File identity and type detection
    ↓
Filename signal analysis
    ↓
Metadata analysis
    ↓
Archive inspection
    ↓
User-rule evaluation
    ↓
Category and destination selection
    ↓
Confidence policy
    ↓
Duplicate and collision handling
    ↓
Move transaction
    ↓
Audit log and undo record
```

Suggested module layout:

```text
src/jwdm/
├── main.py
├── app/
├── watcher/
├── pipeline/
│   ├── context.py
│   ├── result.py
│   ├── runner.py
│   └── stages/
├── classification/
├── services/
├── persistence/
└── ui/
```

Names may evolve, but separation of responsibilities is fixed.


## 9. Classification System

Classification should be layered. General priority:

1. Explicit user rules
2. Folder-specific rules
3. Strong file signatures or format identity
4. Archive content inspection
5. Filename keywords and patterns
6. Extension mapping
7. Broad fallback category
8. Review queue

Do not use AI or remote services in the MVP. Classification must work offline and must not upload filenames or file contents.

### 9.1 User rules

Rules should support combinations such as:

- Extension equals
- Filename contains
- Filename matches wildcard or regular expression
- Source folder equals
- Actual file type equals
- File size range
- Archive contains a path or filename
- Image dimensions or aspect ratio
- Metadata signal exists
- Windows origin metadata exists, when available

Actions may include:

- Route to category
- Route to custom destination
- Ignore
- Always ask
- Tag for review
- Preserve parent folder
- Rename using a template, deferred until later

Explicit user rules override built-in defaults.

### 9.2 Categories

Categories are configurable and should not be inseparably hard-coded.

Possible starter categories:

```text
Blender/
├── Projects/
├── Models/
├── Textures/
└── Addons/

Images/
Documents/
Archives/
Installers/
Audio/
Video/
Code/
Fonts/
Roblox/
Minecraft/
Unsorted/
```

Important skepticism:

- `.blend` is strongly Blender-specific.
- `.fbx`, `.obj`, `.gltf`, `.glb`, `.stl`, and similar formats are generic 3D formats, not inherently Blender files.
- JWDM may ship a creator-oriented starter profile that routes them to `Blender\Models`, but this must be a configurable rule rather than universal truth.
- ZIP archives must not be classified as Blender addons solely by extension.

### 9.3 Images

Images are ambiguous. The MVP may place ordinary image formats in a broad `Images` category.

Later refinements may detect:

- Texture maps through suffixes such as `_normal`, `_roughness`, `_metalness`, `_ao`, `_albedo`, `_diffuse`, `_height`, and `_emissive`
- Screenshots by filename patterns
- Photos through EXIF data
- Icons through file type, dimensions, and aspect ratio
- Animated images
- Image families and PBR texture sets

Uncertain image classification should remain broad rather than pretending certainty.

### 9.4 Archive inspection

Inspect safe metadata and member names without extracting the entire archive.

Possible signals:

- Blender addon: `__init__.py`, `bl_info`, operator/panel modules
- Source project: `package.json`, `pyproject.toml`, `src/`, `.gitignore`
- Asset pack: `textures/`, `models/`, `materials/`
- Minecraft mod: format-specific metadata
- Roblox project/package: recognizable Roblox/Rojo files

Defend against path traversal names, huge member counts, decompression bombs, corrupt indexes, password protection, and unsupported formats.

Archive intelligence may be deferred, but its pipeline slot should exist.

## 10. Collision and Duplicate Handling

Never overwrite silently.

Possible policies:

- Ask every time
- Keep both with safe numbering
- Keep newest
- Keep largest
- Skip incoming file
- Replace only after backup
- Detect exact duplicates and keep one

Default behavior should be conservative: keep both or ask.

Example:

```text
chair.fbx
chair (1).fbx
chair (2).fbx
```

Hashing rules:

- Do not hash every huge file before every move.
- Use metadata as a quick prefilter.
- Calculate a strong hash when exact duplicate detection or collision resolution requires it.
- Hash work should be cancellable and visible.
- Never delete a duplicate without an explicit policy and recovery support.

## 11. Move Transaction and Undo

A move is a transaction:

1. Validate current source state.
2. Validate destination and free space.
3. Resolve collision policy.
4. Create an intended-operation record.
5. Move, or copy-and-verify across volumes.
6. Verify destination exists and expected size matches.
7. Mark operation complete.
8. Create undo record.
9. Remove temporary operation state.

Cross-volume moves must not delete the source until the destination is verified.

Undo records should include:

- Operation ID
- Original path
- Destination path
- Timestamp
- File size
- Hash when available
- Category
- Classification reasons
- Rule ID
- Collision decision
- Whether volumes differed
- Undo status

Undo must detect if either path changed after the original move and ask rather than overwriting newer data.

## 12. User Interface

Preferred GUI toolkit: **PySide6**, unless a documented compatibility or packaging issue justifies another choice.

The UI should feel like a polished Windows utility, not a developer dashboard.

### 12.1 Main window

Suggested elements:

- Large **Organize** button
- Automatic Organization toggle
- Pause/Resume control
- Incoming folders
- Library destination
- Pending candidates
- Needs Review count
- Recent activity
- Destination availability
- Links to History, Rules, and Settings

### 12.2 Review plan

Support:

- Group by proposed category
- Search and filter
- Preview source and destination
- Multi-select approval
- Per-file destination correction
- Create rule from correction
- Ignore file
- Exclude source folder
- Display reason and confidence
- Show total size and item count
- Cancel without changes

### 12.3 System tray

Tray menu:

- Open JWDM
- Organize folders
- Pause automatic organization
- Resume automatic organization
- Pending/Review counts
- Open incoming folder
- Open library
- Settings
- Exit

Closing behavior depends on settings.

## 13. Settings

### General

- Start with Windows
- Launch minimized
- Minimize to tray on close
- Close button exits application
- Show Windows notifications
- Start automatic organization when JWDM launches
- Confirm before exiting while monitoring
- Theme: system, light, dark, if practical
- Language-ready structure, even if only English initially

### Incoming and destination folders

- Add/remove incoming folders
- Choose organized library
- Organize-in-place mode
- Relocate Windows Downloads folder
- Restore previous Windows Downloads folder
- Scan existing files
- Top-level or recursive scan
- Excluded folders
- Managed folders
- Preserve existing parent-folder grouping

### Automation

- Automatic organization enabled
- Confidence threshold
- Automatically move high-confidence files
- Send uncertain files to review
- Ask before every move
- Quiet period
- Stability sample interval/count
- Retry and backoff policy
- Process files created while JWDM was closed
- Pause when destination is unavailable

### Collision and duplicates

- Default collision policy
- Exact duplicate detection
- Hash threshold or on-demand behavior
- Numbering style
- Backup-before-replace behavior

### History and storage

- History retention
- Log level
- Database maintenance
- Export/import rules and settings
- Clear history without deleting files
- Open log folder

### 13.1 Start with Windows

Implement without unnecessary administrator rights.

The startup mechanism must:

- Target the installed or current executable path.
- Enable and disable reliably.
- Avoid duplicate entries.
- Respect launch-minimized and automatic-monitoring settings.
- Detect stale entries after the executable moves.
- Be testable without forcing a reboot.

### 13.2 Minimize to tray on close

When enabled:

- The close button hides the main window.
- The process remains active.
- A first-time notice explains that JWDM is still running.
- The tray menu provides a real Exit action.
- Windows shutdown/logoff is handled cleanly.

## 14. Persistence

Use a small local database, likely SQLite, for operational state.

Suitable content:

- Candidate queue
- Move history
- Undo records
- Rules
- Incoming folders
- Managed destinations
- Exclusions
- Volume identities
- Schema version
- Pending operations

Settings may use a structured local file or SQLite. Choose one coherent approach and document it.

Requirements:

- Atomic writes
- Schema migrations
- Corruption-aware startup
- Human-accessible export for rules/settings
- No cloud requirement
- No telemetry by default
- Do not store file contents

Recommended user-data location:

```text
%LOCALAPPDATA%\JWDM\
```

Do not store mutable state beside the executable.

## 15. Windows and Filesystem Safety

Account for:

- NTFS and non-NTFS external drives
- Drive-letter changes
- Long paths
- Case-insensitive comparisons
- Unicode filenames
- Read-only files
- Permissions failures
- Antivirus locks
- Cloud placeholder files
- Junctions and symbolic links
- Hidden and system files
- Cross-volume moves
- Device disconnect during copy
- Application crash during move
- Windows shutdown during operation

Network paths are initially unsupported or experimental. Do not follow junctions or symlinks recursively by default. Do not delete user data to recover from an operation error.

## 16. Technology Direction

Initial preferred stack:

- Python
- PySide6
- `watchdog` or equivalent filesystem events
- `pathlib`
- SQLite
- Pillow when image metadata is implemented
- Standard-library archive support where adequate
- Windows APIs through `ctypes`, `pywin32`, or a narrowly chosen dependency
- PyInstaller

Dependencies should be justified and restrained.

Code expectations:

- Type hints
- Clear interfaces
- Dataclasses where appropriate
- Structured logging
- Explicit error handling
- Unit-testable services
- No giant god object
- No business logic embedded directly in UI widgets
- No silent `except Exception: pass`


## 17. Build and Test Contract

A root-level PowerShell script is mandatory:

```text
Build.ps1
```

The default developer action is:

```powershell
.\Build.ps1
```

It must:

1. Locate a supported Python installation.
2. Create `.venv` if missing.
3. Install or synchronize dependencies.
4. Stop the previously launched JWDM test executable when safe.
5. Clean stale build artifacts.
6. Build through a tracked PyInstaller spec file.
7. Place output in a predictable location.
8. Surface errors clearly.
9. Launch the fresh compiled executable after success.
10. Return a nonzero exit code on failure.

Recommended commands:

```powershell
.\Build.ps1
.\Build.ps1 -Clean
.\Build.ps1 -Release
.\Build.ps1 -NoLaunch
```

Recommended output:

```text
dist\
├── test\
│   └── JWDM\
│       └── JWDM.exe
└── release\
    └── JWDM.exe
```

Build modes:

- **Default/test:** PyInstaller `onedir` for faster iteration and easier inspection.
- **Release:** PyInstaller `onefile` only if startup, extraction, and antivirus behavior are acceptable. A proper installer may eventually be preferable.

PyInstaller configuration must live in:

```text
JWDM.spec
```

Do not bury packaging logic in a huge disposable PowerShell command.

The build should eventually include:

- Application icon
- Windows version metadata
- Product name
- File description
- Author/company metadata
- GUI subsystem with no console window in normal builds
- Required Qt resources/plugins
- Clean error logging when the GUI cannot start

Direct unit-test execution remains useful, but feature acceptance includes launching and using the compiled executable.

## 18. Repository Structure

Proposed layout:

```text
JWDM/
├── AGENTS.md
├── PROJECT_CONTEXT.md
├── README.md
├── LICENSE
├── Build.ps1
├── JWDM.spec
├── pyproject.toml
├── requirements.lock
├── assets/
├── src/
│   └── jwdm/
│       ├── __init__.py
│       ├── main.py
│       ├── app/
│       ├── watcher/
│       ├── pipeline/
│       ├── classification/
│       ├── services/
│       ├── persistence/
│       └── ui/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/
└── docs/
```

Do not create every empty module merely to imitate the tree. Create structure as responsibilities become real.

## 19. Testing Strategy

### 19.1 Unit tests

- Path normalization
- Parent/child overlap validation
- Managed-folder exclusion
- Temporary suffix recognition
- Stability-state transitions
- Candidate deduplication
- Rule priority
- Destination resolution
- Collision naming
- Undo validation
- External-volume state changes

### 19.2 Integration tests

Use temporary directories to simulate:

- A file gradually increasing in size
- Temporary file renamed to final name
- File locked during download
- File unlocked after delay
- Many duplicate modification events
- Same-volume move
- Cross-volume-like copy/verify abstraction
- Destination disappearing
- Existing-folder scan
- Recursive scan with exclusions
- Restart with pending candidates
- Crash recovery around transaction boundaries

### 19.3 Manual executable checks

Each meaningful milestone should be tested through `Build.ps1` and the built executable.

Checklist:

- Launch
- Tray behavior
- Close behavior
- Start-with-Windows toggling
- Manual folder selection
- Scan preview
- Automatic monitoring
- Active download left untouched
- Completed download processed
- Collision handling
- Undo
- Destination disconnect/reconnect
- Settings persistence

## 20. Security and Privacy

- Operate locally by default.
- Do not upload filenames, paths, metadata, or contents.
- Do not execute downloaded files.
- Do not import or run code from archives.
- Treat filenames and archive entries as untrusted input.
- Avoid shell-command construction from filenames.
- Validate all destinations.
- Do not require elevation for ordinary use.
- Any future update checker must authenticate downloads and verify integrity.

## 21. MVP Scope

### Required MVP capabilities

1. PySide6 main window.
2. Manual **Organize** button with one or more folder selections.
3. Top-level scan with optional recursive scan.
4. Preview organization plan before moving.
5. Configurable organized-library destination.
6. Automatic monitoring for at least one incoming folder.
7. Candidate registry.
8. Temporary-file detection.
9. Stability/quiet-period readiness gate.
10. Conservative Windows file-access probe.
11. Extension-based classification.
12. User-editable basic extension rules.
13. Broad starter categories.
14. Safe collision handling with no overwrite.
15. Transactional moves.
16. History.
17. Undo.
18. Pause/resume automatic mode.
19. System tray.
20. Start with Windows.
21. Minimize to tray on close.
22. Persistent settings.
23. External-destination unavailable state.
24. Root `Build.ps1`.
25. Tracked PyInstaller spec.
26. Compiled executable launches successfully.

### Deferred until after MVP

- Smart archive classification
- PBR texture-family recognition
- EXIF-based photo classification
- Browser integrations
- Download URL/source-site rules
- Multiple automatic incoming folders, unless easy to support correctly
- Windows Downloads relocation, unless shell behavior is first researched and tested carefully
- Cloud sync
- AI classification
- Plugin marketplace
- Automatic online rule downloads
- Complex renaming templates
- Full installer and auto-updater

Architecture should leave room for these without prematurely implementing them.

## 22. MVP Acceptance Criteria

The MVP is acceptable when:

- `.\Build.ps1` from a clean clone produces and launches a usable `JWDM.exe`.
- A user can select a folder, preview organization, approve moves, and undo one.
- Automatic mode detects a newly arriving file.
- A file still changing in size is not moved.
- A recognized partial download is not moved.
- A stable, accessible file is eventually classified and moved.
- Repeated events do not create duplicate jobs.
- JWDM never processes its own destination move as a new incoming file.
- Existing destination files are never overwritten silently.
- If the destination disappears, the source remains safe and the operation defers.
- Closing follows configured tray behavior.
- Start-with-Windows enables/disables without duplicate entries.
- Settings and history survive restart.
- Logs are sufficient to diagnose failed moves.

## 23. Development Phases

### Phase 0: Foundation and executable shell

**Status:** Complete as of 2026-07-22. The canonical onedir test build was
verified through `Build.ps1`; the compiled executable started its main window
and system-tray shell and emitted structured startup logs.

- Create project metadata and package structure.
- Add a minimal PySide6 window.
- Add tray support stub.
- Add logging and crash capture.
- Create `Build.ps1` and `JWDM.spec`.
- Confirm compiled test executable launches.
- Add a minimal automated test command.
- Do not implement the complete organizer yet.

### Phase 1: Manual scan and preview

**Status:** Complete as of 2026-07-22. The manual workflow supports multiple
source folders, per-source recursion, read-only preview planning, conservative
extension classification, safe category correction, explicit per-file
approval, keep-both collision handling, journaled moves, history display, and
validated undo. Phase 1 acceptance is covered by automated tests and the
canonical compiled test build.

- Folder picker
- Scan service
- Path validation
- Extension classification
- Preview plan
- Safe move transaction
- History and undo

### Phase 2: Automatic watcher and readiness

**Status:** Complete as of 2026-07-22. One session-configured, nonrecursive
incoming folder is monitored through watchdog. Events are deduplicated into an
in-memory candidate registry, temporary names defer, stability and quiet-period
sampling reset on changes, a restrictive Win32 access probe gates readiness,
known high-confidence types move through the Phase 1 transaction service, and
unknown types remain queued for review. Main-window and tray controls provide
pause/resume and pending-state visibility.

- Watcher
- Candidate registry
- Temporary detection
- Stability sampling
- Lock/access probe
- Pause/resume
- Pending UI

### Phase 3: Rules and settings

**Status:** Complete as of 2026-07-22. Basic user extension rules support route,
review, and ignore actions ahead of built-in classification. A migrated SQLite
state database persists configured paths, settings, exclusions, rules, and
pending candidate paths. Automatic confidence policy, opt-in existing-file
catch-up, per-user Windows startup registration, launch-minimized behavior, and
configurable close-to-tray behavior are wired through the main window and tray.
Restored candidates restart readiness sampling rather than inheriting stale
safety observations.

- Rule editor
- Start with Windows
- Tray-close behavior
- Confidence policy
- Exclusions
- Persistence and migrations

### Phase 4: External library resilience

- Volume identity
- Destination disconnect/reconnect
- Free-space checks
- Cross-volume copy verification
- Pending-operation recovery

### Phase 5: Smarter classification

- Archive inspection
- Image metadata
- Texture naming patterns
- Suggested rules
- Corrections that become rules

### Phase 6: Downloads relocation and release polish

- Windows known-folder relocation
- Restore flow
- Installer
- Version metadata
- Signed release strategy
- Update strategy

## 24. Fixed Decisions

Treat these as settled unless the owner explicitly changes them:

- Project name is JWDM.
- Expanded name is Jeebus' Windows Download Manager.
- Repository is `WoahItsJeebus/JWDM`.
- The app is Windows-first.
- Python is the implementation language.
- AutoHotkey is not the application foundation.
- A compiled executable is the primary interactive test target.
- `Build.ps1` is mandatory and one-click.
- PyInstaller configuration is tracked.
- Processing is a modular staged pipeline.
- No file moves before readiness passes.
- Manual **Organize** and automatic operation are both core workflows.
- Manual organization accepts one or more selected folders.
- Existing files can be scanned and organized.
- Incoming and organized-library paths are separate concepts.
- Organize-in-place remains supported.
- Windows Downloads relocation is optional.
- Every move is logged and undoable.
- Silent overwrite is forbidden.
- External-destination failure pauses rather than falls back.
- `Start with Windows` and `Minimize to tray on close` are required.
- The MVP is offline and does not use AI classification.

## 25. Open Decisions

Do not silently decide these without recording the choice:

- Final visual design and icon
- License
- Whether Downloads relocation lands in MVP or post-MVP
- Release format: one-file, one-folder plus installer, or both
- History retention default
- Quiet-period defaults after real-world testing
- Network-folder support
- Code-signing approach

### 25.1 Resolved foundation decisions

- Phase 0 supports 64-bit CPython 3.12. Project metadata expresses
  `>=3.12,<3.13`, and `Build.ps1` selects and validates that interpreter.
- Phase 0 dependencies are captured as exact pins in `requirements.lock`,
  resolved from the supported Windows/Python environment. No additional
  dependency-locking tool is introduced at this stage.

### 25.2 Resolved Phase 1 decisions

- The initial built-in category profile is conservative and general-purpose.
  Blender `.blend` projects use `Blender/Projects`; generic interchange formats
  such as FBX, OBJ, glTF, and STL use `3D Models` rather than being presented as
  inherently Blender-specific. Unknown extensions require review.
- Phase 1 collision handling defaults to numbered keep-both destinations. No
  existing destination is replaced or deleted.
- Phase 1 move and undo transitions use an append-only JSON Lines journal under
  `%LOCALAPPDATA%\JWDM\history.jsonl`. This is intentionally limited operational
  history. Phase 3 subsequently resolved settings storage as a separate SQLite
  database while retaining this transaction journal.
- The organized-library choice was session-only through Phase 2. Phase 3 now
  persists it in the per-user state database.
- Phase 1 executes and undoes same-volume moves only. Cross-volume requests are
  explicitly deferred without moving the source; verified cross-volume copy and
  external-library resilience remain Phase 4 work.

### 25.3 Resolved Phase 2 decisions

- Phase 2 uses watchdog 6.0.0 and monitors one top-level incoming folder per
  session. Existing files are not automatically scanned when monitoring starts;
  Phase 3 adds an explicit opt-in setting for closed-app catch-up.
- The active candidate registry remains thread-safe and in memory. Phase 3
  persists pending paths across restarts and resets their readiness sampling;
  crash recovery for pending filesystem move operations remains Phase 4 scope.
- The provisional readiness defaults are a 750 ms sample interval, four stable
  samples, and a minimum three-second quiet period. Size or modification-time
  changes restart sampling, and any filesystem event restarts the quiet window.
- The Windows access gate calls `CreateFileW` with `GENERIC_READ` and a zero
  share mode, then closes the handle immediately. Access denied, sharing
  violations, antivirus contention, and other open failures defer with backoff
  rather than fail or move the candidate.
- Automatic mode moves only built-in high-confidence extension matches after all
  readiness gates pass. Unknown or lower-confidence classifications remain in
  place as `Needs review`.
- Watcher events caused by both manual and automatic JWDM moves are suppressed
  by normalized source/destination identities for a short bounded period.

### 25.4 Resolved Phase 3 decisions

- Phase 3 uses `%LOCALAPPDATA%\JWDM\state.db`, a standard-library SQLite
  database with explicit `PRAGMA user_version` migrations. It stores settings,
  configured paths, basic extension rules, exclusions, and pending candidate
  paths. The append-only `history.jsonl` move/undo journal remains separate so
  filesystem transaction history keeps its existing recovery semantics.
- Basic user rules match filename extensions and support route-to-category,
  require-review, and ignore actions. Enabled user rules are evaluated before
  built-in extension mappings. More complex metadata and archive rules remain
  in their later phases.
- Automatic confidence policy initially offers two conservative choices: move
  recognized built-in or explicit-rule matches after readiness passes, or send
  every automatic candidate to review. Unknown formats never auto-move.
- Configured exclusions match an exact normalized path or descendants of an
  excluded folder. They apply to manual recursion and automatic candidates.
- Start with Windows uses the current user's
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value, targets the
  current executable, repairs stale commands, and requires no elevation.
- Close-to-tray is enabled by default when a system tray is available and shows
  a one-time notice. The tray Exit action remains an unconditional real exit.
- Pending candidate paths persist across restarts. Restored candidates discard
  prior stability/access observations and pass the full readiness pipeline
  again. Scanning other top-level files that arrived while JWDM was closed is an
  explicit opt-in setting.

When a decision is made, update this document.

## 26. First Codex Task

> Read `AGENTS.md` and `PROJECT_CONTEXT.md`. Create Phase 0 only. Scaffold a maintainable Python package, a minimal PySide6 main window showing the JWDM name, a functioning system-tray icon/menu stub, structured file logging, a root `Build.ps1`, and a tracked `JWDM.spec`. The default PowerShell command must create or update a local virtual environment, install dependencies, build a PyInstaller onedir test executable, and launch it. Add a small smoke test. Do not implement folder watching, classification, file moving, Windows Downloads relocation, or the full settings UI yet. Run the tests and the build, inspect failures, and report exactly what was created and verified.

Phase 0 is complete only when the built executable launches successfully and the repository remains understandable.

## 27. Documentation Maintenance

This is the long-form product and architecture brief.

Update it when:

- A product decision changes
- A phase is completed
- A major technical approach changes
- A deferred feature enters scope
- A safety invariant is added
- A new open question appears

Keep `AGENTS.md` short and operational. Keep this document comprehensive.
