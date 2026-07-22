from __future__ import annotations

from pathlib import Path

import pytest

from jwdm.services.destinations import (
    CategoryValidationError,
    destination_for,
    resolve_collision,
    validate_category,
)


@pytest.mark.parametrize("category", ["", "../Elsewhere", "CON", "Bad?Name", "/Absolute"])
def test_category_validation_rejects_unsafe_paths(category: str) -> None:
    with pytest.raises(CategoryValidationError):
        validate_category(category)


def test_nested_category_stays_inside_library(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()

    destination = destination_for(library, "Blender/Projects", "scene.blend")

    assert destination == library / "Blender" / "Projects" / "scene.blend"


def test_collision_uses_keep_both_numbering(tmp_path: Path) -> None:
    base = tmp_path / "report.pdf"
    base.write_text("existing", encoding="utf-8")
    (tmp_path / "report (1).pdf").write_text("also existing", encoding="utf-8")

    destination, behavior = resolve_collision(base)

    assert destination.name == "report (2).pdf"
    assert behavior == "numbered_keep_both"

