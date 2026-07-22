"""Nonrecursive watchdog adapter for one incoming folder."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from jwdm.watcher.events import FileWatchEvent, WatchEventType


class SuppressionLookup(Protocol):
    def contains(self, path: Path) -> bool: ...


class WatcherError(RuntimeError):
    """The incoming-folder observer could not start or stop safely."""


class _IncomingEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        root: Path,
        callback: Callable[[FileWatchEvent], None],
        suppressor: SuppressionLookup,
    ) -> None:
        super().__init__()
        self._root = root
        self._callback = callback
        self._suppressor = suppressor

    def on_created(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileCreatedEvent):
            self._emit(FileWatchEvent(WatchEventType.CREATED, Path(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileModifiedEvent):
            self._emit(FileWatchEvent(WatchEventType.MODIFIED, Path(event.src_path)))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if isinstance(event, FileDeletedEvent):
            self._emit(FileWatchEvent(WatchEventType.DELETED, Path(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not isinstance(event, FileMovedEvent):
            return
        source = Path(event.src_path)
        destination = Path(event.dest_path)
        if not self._is_direct_child(destination):
            self._emit(FileWatchEvent(WatchEventType.DELETED, source))
            return
        self._emit(FileWatchEvent(WatchEventType.MOVED, source, destination))

    def _emit(self, event: FileWatchEvent) -> None:
        paths = (event.source,) if event.destination is None else (event.source, event.destination)
        if any(self._suppressor.contains(path) for path in paths):
            return
        if event.event_type is not WatchEventType.DELETED and event.destination is None:
            if not self._is_direct_child(event.source):
                return
        self._callback(event)

    def _is_direct_child(self, path: Path) -> bool:
        try:
            return path.resolve(strict=False).parent == self._root
        except OSError:
            return False


class DirectoryWatcher:
    """Watch one normalized root without following or monitoring subfolders."""

    def __init__(
        self,
        root: Path,
        callback: Callable[[FileWatchEvent], None],
        suppressor: SuppressionLookup,
    ) -> None:
        self._root = root.resolve(strict=True)
        self._handler = _IncomingEventHandler(self._root, callback, suppressor)
        self._observer = Observer()
        self._started = False

    def start(self) -> None:
        if self._started:
            raise WatcherError("Incoming-folder watcher is already running.")
        try:
            self._observer.schedule(self._handler, str(self._root), recursive=False)
            self._observer.start()
        except OSError as error:
            raise WatcherError(f"Cannot watch incoming folder {self._root}: {error}") from error
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._observer.stop()
        self._observer.join(timeout=5)
        if self._observer.is_alive():
            raise WatcherError("Incoming-folder watcher did not stop within five seconds.")
        self._started = False
