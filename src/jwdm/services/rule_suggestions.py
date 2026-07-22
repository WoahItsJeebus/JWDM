"""Turn explicit review corrections into optional durable extension rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jwdm.config import ExtensionRule, RuleAction, normalize_extension
from jwdm.services.destinations import validate_category


class RuleSuggestionError(ValueError):
    """A requested set of correction-derived rules is ambiguous or invalid."""


@dataclass(frozen=True, slots=True)
class CategoryCorrection:
    source: Path
    category: str
    create_rule: bool = False


class RuleWriter(Protocol):
    def upsert_rules(self, rules: tuple[ExtensionRule, ...]) -> None: ...


def suggested_extension(path: Path) -> str | None:
    """Return a conservative extension target for a possible user rule."""

    suffixes = tuple(suffix.casefold() for suffix in path.suffixes)
    if not suffixes:
        return None
    candidate = suffixes[-1]
    if len(suffixes) >= 2 and suffixes[-2] == ".tar":
        candidate = "".join(suffixes[-2:])
    try:
        return normalize_extension(candidate)
    except ValueError:
        return None


class RuleSuggestionService:
    """Validate, deduplicate, and persist only explicitly requested suggestions."""

    def __init__(self, repository: RuleWriter) -> None:
        self._repository = repository

    def suggestions(
        self, corrections: tuple[CategoryCorrection, ...]
    ) -> tuple[ExtensionRule, ...]:
        proposed: dict[str, ExtensionRule] = {}
        for correction in corrections:
            if not correction.create_rule:
                continue
            extension = suggested_extension(correction.source)
            if extension is None:
                raise RuleSuggestionError(
                    f"No safe extension rule can be created for {correction.source.name}."
                )
            try:
                category = validate_category(correction.category)
            except ValueError as error:
                raise RuleSuggestionError(str(error)) from error
            existing = proposed.get(extension)
            if existing is not None and existing.category != category:
                raise RuleSuggestionError(
                    f"Corrections disagree about where {extension} files should go."
                )
            proposed[extension] = ExtensionRule(
                extension=extension,
                action=RuleAction.ROUTE,
                category=category,
                enabled=True,
                priority=100,
            )
        return tuple(proposed[key] for key in sorted(proposed))

    def save(self, suggestions: tuple[ExtensionRule, ...]) -> None:
        if suggestions:
            self._repository.upsert_rules(suggestions)
