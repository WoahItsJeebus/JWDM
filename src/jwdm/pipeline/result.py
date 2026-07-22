"""Explicit stage results shared by automatic processing gates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageOutcome(StrEnum):
    PASS = "PASS"
    DEFER = "DEFER"
    REJECT = "REJECT"
    REVIEW = "REVIEW"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class StageResult:
    outcome: StageOutcome
    reason: str
    error_code: int | None = None

