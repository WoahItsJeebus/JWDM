from __future__ import annotations

from pathlib import Path

import pytest

from jwdm.persistence.history import HistoryError, HistoryRepository


def test_corrupt_history_is_reported_explicitly(tmp_path: Path) -> None:
    path = tmp_path / "history.jsonl"
    path.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(HistoryError, match="Invalid JSON"):
        HistoryRepository(path).operations()
