from __future__ import annotations

from pathlib import Path

from jwdm.classification.rule_classifier import RuleClassifier
from jwdm.config import ExtensionRule, RuleAction
from jwdm.persistence.state import StateRepository
from jwdm.pipeline.models import ClassificationDisposition, PlanItemStatus, ScanRoot
from jwdm.services.exclusions import ExclusionMatcher
from jwdm.services.scan import ScanService


def test_user_rules_override_builtins_with_route_review_and_ignore(tmp_path: Path) -> None:
    repository = StateRepository(tmp_path / "state.db")
    repository.replace_rules(
        (
            ExtensionRule(".pdf", RuleAction.REVIEW),
            ExtensionRule(".thing", RuleAction.ROUTE, "Custom/Things"),
            ExtensionRule(".bak", RuleAction.IGNORE),
        )
    )
    classifier = RuleClassifier(repository)

    review = classifier.classify(Path("report.pdf"))
    routed = classifier.classify(Path("model.thing"))
    ignored = classifier.classify(Path("old.bak"))
    fallback = classifier.classify(Path("photo.png"))

    assert review.disposition is ClassificationDisposition.REVIEW
    assert routed.category == "Custom/Things"
    assert routed.confidence == "user"
    assert ignored.disposition is ClassificationDisposition.EXCLUDE
    assert fallback.category == "Images"


def test_manual_scan_marks_exact_exclusions_and_ignored_rules(tmp_path: Path) -> None:
    source = tmp_path / "source"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    excluded = source / "private.pdf"
    ignored = source / "old.bak"
    normal = source / "photo.png"
    for path in (excluded, ignored, normal):
        path.write_bytes(b"data")

    repository = StateRepository(tmp_path / "state.db")
    repository.replace_rules((ExtensionRule(".bak", RuleAction.IGNORE),))
    matcher = ExclusionMatcher(lambda: (excluded,))
    plan = ScanService(
        classifier=RuleClassifier(repository),
        exclusion_matcher=matcher,
    ).build_plan((ScanRoot(source),), library)

    statuses = {item.source.name: item.status for item in plan.items}
    assert statuses == {
        "old.bak": PlanItemStatus.EXCLUDED,
        "photo.png": PlanItemStatus.READY,
        "private.pdf": PlanItemStatus.EXCLUDED,
    }
