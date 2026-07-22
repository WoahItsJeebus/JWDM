from __future__ import annotations

import threading
from pathlib import Path

from jwdm.services.operation_suppression import OperationSuppressor
from jwdm.watcher.directory_watcher import DirectoryWatcher
from jwdm.watcher.events import FileWatchEvent, WatchEventType


def test_watchdog_reports_new_top_level_file(tmp_path: Path) -> None:
    received: list[FileWatchEvent] = []
    ready = threading.Event()

    def collect(event: FileWatchEvent) -> None:
        received.append(event)
        if event.source.name == "new.pdf":
            ready.set()

    watcher = DirectoryWatcher(tmp_path, collect, OperationSuppressor())
    watcher.start()
    try:
        (tmp_path / "new.pdf").write_bytes(b"content")
        assert ready.wait(timeout=5), "watchdog did not report the new file"
    finally:
        watcher.stop()

    assert any(
        event.source.name == "new.pdf"
        and event.event_type in {WatchEventType.CREATED, WatchEventType.MODIFIED}
        for event in received
    )

