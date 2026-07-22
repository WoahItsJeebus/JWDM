"""Normalized user-configured path exclusion matching."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class ExclusionMatcher:
    """Match an exact excluded path or anything below an excluded folder."""

    def __init__(self, provider: Callable[[], tuple[Path, ...]]) -> None:
        self._provider = provider

    def matches(self, path: Path) -> bool:
        candidate = path.expanduser().resolve(strict=False)
        for configured in self._provider():
            exclusion = configured.expanduser().resolve(strict=False)
            if candidate == exclusion or candidate.is_relative_to(exclusion):
                return True
        return False
