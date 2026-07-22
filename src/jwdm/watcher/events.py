"""Typed watcher events independent of the watchdog implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class WatchEventType(StrEnum):
    CREATED = "created"
    MODIFIED = "modified"
    MOVED = "moved"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class FileWatchEvent:
    event_type: WatchEventType
    source: Path
    destination: Path | None = None

