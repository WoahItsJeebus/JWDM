"""Typed application settings and user-rule models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class ConfidencePolicy(StrEnum):
    """Decide whether recognized automatic candidates may move without review."""

    MOVE_RECOGNIZED = "move_recognized"
    REVIEW_ALL = "review_all"


class RuleAction(StrEnum):
    """Supported actions for the MVP extension-rule editor."""

    ROUTE = "route"
    REVIEW = "review"
    IGNORE = "ignore"


class DownloadsRelocationState(StrEnum):
    """Durable checkpoints around a Windows Downloads redirection."""

    PREPARED = "prepared"
    ACTIVE = "active"
    RESTORE_PREPARED = "restore_prepared"
    RESTORED = "restored"
    ROLLED_BACK = "rolled_back"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class ExtensionRule:
    """One explicit user rule, evaluated before built-in extension mappings."""

    extension: str
    action: RuleAction
    category: str | None = None
    enabled: bool = True
    priority: int = 100
    rule_id: int | None = None


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Persisted application preferences and configured paths."""

    library_path: Path | None = None
    incoming_path: Path | None = None
    start_with_windows: bool = False
    launch_minimized: bool = False
    minimize_to_tray: bool = True
    close_notice_shown: bool = False
    start_automatic: bool = False
    process_existing_on_start: bool = False
    confidence_policy: ConfidencePolicy = ConfidencePolicy.MOVE_RECOGNIZED
    exclusions: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class VolumeBinding:
    """Persist a folder's identity independently from its current drive letter."""

    volume_id: str
    relative_path: str
    last_mount_path: Path
    serial_number: int | None = None
    filesystem: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadsRelocationRecord:
    """Restore information persisted before changing the shell known folder."""

    original_path: Path
    relocated_path: Path
    state: DownloadsRelocationState
    created_at: datetime
    updated_at: datetime
    error: str | None = None


_EXTENSION_PATTERN = re.compile(r"^\.[a-z0-9][a-z0-9._+-]{0,31}$")


def normalize_extension(value: str) -> str:
    """Normalize a simple extension rule or raise a user-facing error."""

    extension = value.strip().casefold()
    if extension.startswith("*."):
        extension = extension[1:]
    elif extension and not extension.startswith("."):
        extension = f".{extension}"
    if not _EXTENSION_PATTERN.fullmatch(extension):
        raise ValueError(
            "Extension must look like .pdf or .tar.gz and cannot contain path separators."
        )
    return extension
