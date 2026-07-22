from __future__ import annotations

from pathlib import Path

from jwdm.pipeline.models import PlanItemStatus, ScanProgress, ScanRoot, ScanStage
from jwdm.services.scan import ScanService


def test_top_level_scan_does_not_descend(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    library = tmp_path / "library"
    nested.mkdir(parents=True)
    library.mkdir()
    (source / "report.pdf").write_text("report", encoding="utf-8")
    (nested / "photo.jpg").write_text("image", encoding="utf-8")

    plan = ScanService().build_plan((ScanRoot(source, False),), library)

    assert [item.source.name for item in plan.items] == ["report.pdf"]
    assert plan.items[0].category == "Documents"


def test_recursive_scan_excludes_in_place_library_and_flags_unknown(tmp_path: Path) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    library = source / "Sorted"
    nested.mkdir(parents=True)
    library.mkdir()
    (source / "mystery.unknown").write_text("?", encoding="utf-8")
    (nested / "photo.png").write_text("image", encoding="utf-8")
    (library / "already.pdf").write_text("managed", encoding="utf-8")

    plan = ScanService().build_plan((ScanRoot(source, True),), library)

    names = {item.source.name for item in plan.items}
    assert names == {"mystery.unknown", "photo.png"}
    unknown = next(item for item in plan.items if item.source.name == "mystery.unknown")
    assert unknown.status is PlanItemStatus.REVIEW
    assert unknown.proposed_destination is None


def test_plan_reserves_numbered_collision_destinations(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    library = tmp_path / "library"
    first.mkdir()
    second.mkdir()
    library.mkdir()
    (first / "same.pdf").write_text("one", encoding="utf-8")
    (second / "same.pdf").write_text("two", encoding="utf-8")

    plan = ScanService().build_plan(
        (ScanRoot(first), ScanRoot(second)),
        library,
    )

    destinations = {item.proposed_destination.name for item in plan.items if item.proposed_destination}
    assert destinations == {"same.pdf", "same (1).pdf"}


def test_scan_reports_discovery_then_determinate_classification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    nested = source / "nested"
    library = tmp_path / "library"
    nested.mkdir(parents=True)
    library.mkdir()
    (source / "report.pdf").write_text("report", encoding="utf-8")
    (nested / "photo.png").write_text("image", encoding="utf-8")
    progress: list[ScanProgress] = []

    plan = ScanService().build_plan(
        (ScanRoot(source, True),), library, progress.append
    )

    assert len(plan.items) == 2
    assert progress[0].stage is ScanStage.DISCOVERING
    classification = [event for event in progress if event.stage is ScanStage.CLASSIFYING]
    assert classification[0].completed_items == 0
    assert classification[0].total_items == 2
    assert classification[-1].completed_items == 2
    assert classification[-1].total_items == 2
