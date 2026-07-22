from __future__ import annotations

import zipfile
from pathlib import Path

from PIL import Image

from jwdm.classification.archive_classifier import ArchiveClassifier, ArchiveLimits
from jwdm.classification.rule_classifier import RuleClassifier
from jwdm.classification.smart_classifier import SmartClassifier
from jwdm.config import ExtensionRule, RuleAction
from jwdm.persistence.state import StateRepository
from jwdm.pipeline.models import ClassificationDisposition, PlanItemStatus, ScanRoot
from jwdm.services.scan import ScanService


def test_zip_blender_addon_is_identified_without_extraction(tmp_path: Path) -> None:
    archive_path = tmp_path / "addon.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sample_addon/__init__.py", "bl_info = {'name': 'Sample'}")
        archive.writestr("sample_addon/operators.py", "class SampleOperator: pass")

    result = SmartClassifier().classify(archive_path)

    assert result.category == "Blender/Addons"
    assert result.confidence == "high"
    assert "bl_info" in result.reason
    assert not (tmp_path / "sample_addon").exists()


def test_zip_project_and_game_markers_are_layered_content_signals(
    tmp_path: Path,
) -> None:
    source_project = tmp_path / "source.zip"
    minecraft_mod = tmp_path / "mod.zip"
    roblox_project = tmp_path / "roblox.zip"
    with zipfile.ZipFile(source_project, "w") as archive:
        archive.writestr("project/pyproject.toml", "[project]")
        archive.writestr("project/src/main.py", "pass")
    with zipfile.ZipFile(minecraft_mod, "w") as archive:
        archive.writestr("wrapper/META-INF/mods.toml", "modLoader='javafml'")
    with zipfile.ZipFile(roblox_project, "w") as archive:
        archive.writestr("game/default.project.json", "{}")

    classifier = SmartClassifier()

    assert classifier.classify(source_project).category == "Code"
    assert classifier.classify(minecraft_mod).category == "Minecraft"
    assert classifier.classify(roblox_project).category == "Roblox"


def test_unsafe_or_excessive_zip_metadata_requires_review(tmp_path: Path) -> None:
    traversal = tmp_path / "traversal.zip"
    excessive = tmp_path / "excessive.zip"
    escaped = tmp_path.parent / "jwdm-phase5-should-not-exist.txt"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../jwdm-phase5-should-not-exist.txt", "do not extract")
    with zipfile.ZipFile(excessive, "w") as archive:
        archive.writestr("one.txt", "one")
        archive.writestr("two.txt", "two")

    traversal_result = ArchiveClassifier().classify(traversal)
    excessive_result = ArchiveClassifier(ArchiveLimits(max_members=1)).classify(excessive)

    assert traversal_result is not None
    assert traversal_result.disposition is ClassificationDisposition.REVIEW
    assert traversal_result.category == "Archives"
    assert "unsafe member path" in traversal_result.reason
    assert excessive_result is not None
    assert excessive_result.disposition is ClassificationDisposition.REVIEW
    assert "limit is 1" in excessive_result.reason
    assert not escaped.exists()


def test_manual_plan_preserves_a_safe_review_category_suggestion(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = tmp_path / "library"
    source.mkdir()
    library.mkdir()
    archive_path = source / "asset-pack.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("pack/textures/color.png", "texture")
        archive.writestr("pack/models/chair.obj", "model")

    plan = ScanService().build_plan((ScanRoot(source),), library)

    assert plan.items[0].status is PlanItemStatus.REVIEW
    assert plan.items[0].category == "3D Models"
    assert plan.items[0].proposed_destination is None


def test_texture_filename_signal_is_generic_and_does_not_require_file_access() -> None:
    result = SmartClassifier().classify(Path("BrickWall_roughness_4k.PNG"))

    assert result.category == "Images/Textures"
    assert result.confidence == "high"
    assert "roughness" in result.reason


def test_image_metadata_routes_photos_animations_and_icons(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    animation = tmp_path / "animation.gif"
    icon = tmp_path / "app_icon.png"

    exif = Image.Exif()
    exif[271] = "Example Camera"
    Image.new("RGB", (320, 200), "green").save(photo, exif=exif)
    frames = [Image.new("RGB", (12, 12), color) for color in ("red", "blue")]
    frames[0].save(
        animation,
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    Image.new("RGBA", (64, 64), "purple").save(icon)

    classifier = SmartClassifier()

    assert classifier.classify(photo).category == "Images/Photos"
    assert classifier.classify(animation).category == "Images/Animated"
    assert classifier.classify(icon).category == "Images/Icons"


def test_unreadable_image_header_stays_in_place_for_review(tmp_path: Path) -> None:
    image = tmp_path / "not-really-an-image.png"
    image.write_bytes(b"not image data")

    result = SmartClassifier().classify(image)

    assert result.category == "Images"
    assert result.disposition is ClassificationDisposition.REVIEW
    assert result.confidence == "low"
    assert "only a suggestion" in result.reason


def test_explicit_user_rule_still_overrides_archive_content(tmp_path: Path) -> None:
    archive_path = tmp_path / "addon.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("addon/__init__.py", "bl_info = {}")
    repository = StateRepository(tmp_path / "state.db")
    repository.replace_rules(
        (ExtensionRule(".zip", RuleAction.ROUTE, "Custom/Reviewed Archives"),)
    )

    result = RuleClassifier(repository).classify(archive_path)

    assert result.category == "Custom/Reviewed Archives"
    assert result.confidence == "user"
