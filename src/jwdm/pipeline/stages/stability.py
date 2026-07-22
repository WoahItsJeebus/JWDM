"""Quiet-period and repeated-snapshot readiness gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from jwdm.pipeline.candidate import CandidateSnapshot
from jwdm.pipeline.result import StageOutcome, StageResult


@dataclass(frozen=True, slots=True)
class ReadinessConfig:
    sample_interval_seconds: float = 0.75
    required_stable_samples: int = 4
    minimum_quiet_seconds: float = 3.0
    retry_base_seconds: float = 1.5
    retry_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.sample_interval_seconds <= 0:
            raise ValueError("Sample interval must be positive.")
        if self.required_stable_samples < 2:
            raise ValueError("At least two stable samples are required.")
        if self.minimum_quiet_seconds <= 0:
            raise ValueError("Minimum quiet period must be positive.")


class StabilityStage:
    def __init__(self, config: ReadinessConfig) -> None:
        self._config = config

    def evaluate(self, candidate: CandidateSnapshot, now: datetime) -> StageResult:
        if candidate.last_size is None or candidate.last_modified_ns is None:
            return StageResult(StageOutcome.DEFER, "Waiting for the first file snapshot")
        if candidate.stable_samples < self._config.required_stable_samples:
            return StageResult(
                StageOutcome.DEFER,
                f"Stable samples: {candidate.stable_samples}/{self._config.required_stable_samples}",
            )
        if candidate.stable_since is None:
            return StageResult(StageOutcome.DEFER, "Stability timing has not started")

        quiet_anchor = max(candidate.stable_since, candidate.last_event_at)
        quiet_seconds = (now - quiet_anchor).total_seconds()
        if quiet_seconds < self._config.minimum_quiet_seconds:
            return StageResult(
                StageOutcome.DEFER,
                f"Quiet for {quiet_seconds:.1f}/{self._config.minimum_quiet_seconds:.1f} seconds",
            )
        return StageResult(
            StageOutcome.PASS,
            f"Stable for {candidate.stable_samples} samples and {quiet_seconds:.1f} quiet seconds",
        )

