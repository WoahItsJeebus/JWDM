"""Layer explicit user extension rules over conservative built-in defaults."""

from __future__ import annotations

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
    ) -> None:
        self._rules = rules
        self._fallback = fallback or SmartClassifier()

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
        return self._fallback.classify(path)
