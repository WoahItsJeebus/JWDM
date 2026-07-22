from __future__ import annotations

from pathlib import Path

import pytest

from jwdm.classification.rule_classifier import RuleClassifier
from jwdm.config import ExtensionRule, RuleAction
from jwdm.persistence.state import StateRepository
from jwdm.services.rule_suggestions import (
    CategoryCorrection,
    RuleSuggestionError,
    RuleSuggestionService,
    suggested_extension,
)


def test_explicit_correction_becomes_a_durable_user_rule(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "state.db")
    service = RuleSuggestionService(repository)
    correction = CategoryCorrection(
        tmp_path / "download.widget",
        "Custom/Widgets",
        create_rule=True,
    )

    suggestions = service.suggestions((correction,))
    service.save(suggestions)

    assert len(suggestions) == 1
    assert suggestions[0].extension == ".widget"
    assert RuleClassifier(repository).classify(Path("future.widget")).category == (
        "Custom/Widgets"
    )


def test_suggestion_updates_existing_rule_only_after_explicit_request(
    tmp_path: Path,
) -> None:
    repository = StateRepository(tmp_path / "state.db")
    repository.replace_rules((ExtensionRule(".thing", RuleAction.REVIEW),))
    service = RuleSuggestionService(repository)

    assert service.suggestions(
        (CategoryCorrection(Path("one.thing"), "Custom/Things", create_rule=False),)
    ) == ()
    assert repository.rules()[0].action is RuleAction.REVIEW

    suggestions = service.suggestions(
        (CategoryCorrection(Path("one.thing"), "Custom/Things", create_rule=True),)
    )
    service.save(suggestions)

    saved = repository.rules()[0]
    assert saved.action is RuleAction.ROUTE
    assert saved.category == "Custom/Things"


def test_conflicting_correction_suggestions_are_refused(tmp_path: Path) -> None:
    service = RuleSuggestionService(StateRepository(tmp_path / "state.db"))

    with pytest.raises(RuleSuggestionError, match="disagree"):
        service.suggestions(
            (
                CategoryCorrection(Path("one.asset"), "Models", True),
                CategoryCorrection(Path("two.asset"), "Textures", True),
            )
        )


def test_compound_tar_extension_is_suggested_without_using_the_whole_filename() -> None:
    assert suggested_extension(Path("backup.tar.gz")) == ".tar.gz"
    assert suggested_extension(Path("README")) is None
