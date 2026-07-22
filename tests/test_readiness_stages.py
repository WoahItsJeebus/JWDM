from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jwdm.pipeline.result import StageOutcome
from jwdm.pipeline.stages.stability import ReadinessConfig, StabilityStage
from jwdm.pipeline.stages.temporary import TemporaryFileStage
from jwdm.services.candidate_registry import CandidateRegistry


@pytest.mark.parametrize(
    "filename",
    ["file.crdownload", "file.part", "file.partial", "file.download", "file.tmp", "Unconfirmed 123.crdownload"],
)
def test_temporary_names_are_deferred(filename: str) -> None:
    result = TemporaryFileStage().evaluate(Path(filename))

    assert result.outcome is StageOutcome.DEFER


def test_final_looking_name_passes_temporary_stage() -> None:
    assert TemporaryFileStage().evaluate(Path("file.pdf")).outcome is StageOutcome.PASS


def test_stability_requires_samples_and_quiet_period(tmp_path: Path) -> None:
    config = ReadinessConfig(
        sample_interval_seconds=1,
        required_stable_samples=3,
        minimum_quiet_seconds=2,
    )
    stage = StabilityStage(config)
    registry = CandidateRegistry()
    started = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = registry.register_event(tmp_path / "file.pdf", tmp_path, "created", started)

    first, _ = registry.observe(candidate.candidate_id, 10, 100, started)
    second, _ = registry.observe(candidate.candidate_id, 10, 100, started + timedelta(seconds=1))
    third, _ = registry.observe(candidate.candidate_id, 10, 100, started + timedelta(seconds=2))

    assert first is not None and stage.evaluate(first, started).outcome is StageOutcome.DEFER
    assert second is not None and stage.evaluate(second, started + timedelta(seconds=1)).outcome is StageOutcome.DEFER
    assert third is not None
    assert stage.evaluate(third, started + timedelta(seconds=2)).outcome is StageOutcome.PASS


def test_repeated_event_restarts_quiet_period(tmp_path: Path) -> None:
    config = ReadinessConfig(1, 2, 2)
    stage = StabilityStage(config)
    registry = CandidateRegistry()
    started = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = registry.register_event(tmp_path / "file.pdf", tmp_path, "created", started)
    registry.observe(candidate.candidate_id, 10, 100, started)
    registry.observe(candidate.candidate_id, 10, 100, started + timedelta(seconds=1))
    registry.register_event(
        tmp_path / "file.pdf", tmp_path, "modified", started + timedelta(seconds=1.5)
    )
    snapshot, _ = registry.observe(
        candidate.candidate_id, 10, 100, started + timedelta(seconds=2)
    )

    assert snapshot is not None
    assert stage.evaluate(snapshot, started + timedelta(seconds=2)).outcome is StageOutcome.DEFER

