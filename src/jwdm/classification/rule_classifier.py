"""Layer explicit user extension rules over conservative built-in defaults."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from jwdm.classification.smart_classifier import SmartClassifier
from jwdm.config import ExtensionRule, RuleAction
from jwdm.pipeline.models import Classification, ClassificationDisposition


class RuleProvider(Protocol):
    def rules(self) -> tuple[ExtensionRule, ...]: ...


class Classifier(Protocol):
    def classify(self, path: Path) -> Classification: ...


class RuleClassifier:
    """Evaluate enabled user rules first, then use layered offline signals."""

    def __init__(
        self,
        rules: RuleProvider,
        fallback: Classifier | None = None,
        route_unknown: Callable[[], bool] | None = None,
    ) -> None:
        self._rules = rules
        self._fallback = fallback or SmartClassifier()
        self._route_unknown = route_unknown or (lambda: False)

    def classify(self, path: Path) -> Classification:
        filename = path.name.casefold()
        for rule in self._rules.rules():
            if not rule.enabled or not filename.endswith(rule.extension.casefold()):
                continue
            identity = f"user rule {rule.rule_id}" if rule.rule_id is not None else "user rule"
            if rule.action is RuleAction.ROUTE:
                return Classification(
                    category=rule.category,
                    confidence="user",
                    reason=f"Explicit {identity}: {rule.extension} → {rule.category}",
                )
            if rule.action is RuleAction.IGNORE:
                return Classification(
                    category=None,
                    confidence="user",
                    reason=f"Explicit {identity} ignores {rule.extension}",
                    disposition=ClassificationDisposition.EXCLUDE,
                )
            return Classification(
                category=None,
                confidence="user",
                reason=f"Explicit {identity} requires review for {rule.extension}",
                disposition=ClassificationDisposition.REVIEW,
            )
        fallback = self._fallback.classify(path)
        if (
            self._route_unknown()
            and fallback.category is None
            and fallback.disposition is ClassificationDisposition.REVIEW
            and fallback.reason.startswith("No built-in rule for ")
        ):
            return Classification(
                category="Unknown",
                confidence="user",
                reason="Unknown-folder setting routes files without a matching rule to Unknown",
            )
        return fallback
