"""Recognition of browser and application partial-download names."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from jwdm.pipeline.result import StageOutcome, StageResult

TEMPORARY_SUFFIXES: Final = frozenset(
    {".crdownload", ".part", ".partial", ".download", ".tmp"}
)


class TemporaryFileStage:
    def evaluate(self, path: Path) -> StageResult:
        name = path.name.casefold()
        suffix = path.suffix.casefold()
        if suffix in TEMPORARY_SUFFIXES or (
            name.startswith("unconfirmed ") and name.endswith(".crdownload")
        ):
            return StageResult(
                StageOutcome.DEFER,
                f"Recognized temporary or partial-download name: {path.name}",
            )
        return StageResult(StageOutcome.PASS, "Filename does not match a partial-download pattern")

