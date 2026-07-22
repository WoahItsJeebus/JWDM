from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch
from PySide6.QtWidgets import QApplication, QMessageBox

from jwdm.app.manual_organize import ManualOrganizeController
from jwdm.persistence.history import HistoryRepository
from jwdm.pipeline.models import ScanPlan, ScanProgress, ScanRoot, ScanStage
from jwdm.services.move_transaction import MoveTransactionService
from jwdm.ui.main_window import MainWindow


class _ControlledScanner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.thread_id: int | None = None

    def build_plan(
        self,
        roots: tuple[ScanRoot, ...],
        library_root: Path,
        progress_callback: Callable[[ScanProgress], None],
    ) -> ScanPlan:
        self.thread_id = threading.get_ident()
        self.started.set()
        progress_callback(
            ScanProgress(ScanStage.DISCOVERING, 1, None, roots[0].path)
        )
        self.release.wait(timeout=5)
        progress_callback(ScanProgress(ScanStage.CLASSIFYING, 0, 0, roots[0].path))
        return ScanPlan(roots, library_root, (), (), datetime.now(UTC))


def test_manual_scan_runs_off_ui_thread_and_updates_progress(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    application = QApplication.instance() or QApplication([])
    window = MainWindow()
    scanner = _ControlledScanner()
    history = HistoryRepository(tmp_path / "history.jsonl")
    controller = ManualOrganizeController(
        window,
        scanner,  # type: ignore[arg-type]
        MoveTransactionService(history),
        history,
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    source = tmp_path / "source"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()

    controller._begin_scan((ScanRoot(source),), library)
    assert scanner.started.wait(timeout=2)
    application.processEvents()

    assert controller.scan_in_progress
    assert scanner.thread_id != threading.get_ident()
    assert window.manual_scan_progress.maximum() == 0
    assert not window.organize_button.isEnabled()

    scanner.release.set()
    deadline = time.monotonic() + 3
    while controller.scan_in_progress and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)

    assert not controller.scan_in_progress
    assert window.manual_scan_progress.value() == 0
    assert window.manual_scan_progress.format() == "Complete \u2014 %v files"
    assert "complete" in window.manual_scan_status_label.text().lower()
    assert window.organize_button.isEnabled()
    window.close()
