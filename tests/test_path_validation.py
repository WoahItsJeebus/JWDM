from __future__ import annotations

from pathlib import Path

import pytest

from jwdm.pipeline.models import ScanRoot
from jwdm.services.path_validation import PathValidationError, PathValidator


def test_source_and_library_cannot_be_identical(tmp_path: Path) -> None:
    with pytest.raises(PathValidationError, match="cannot be the same"):
        PathValidator().validate((ScanRoot(tmp_path),), tmp_path)


def test_source_inside_library_is_rejected(tmp_path: Path) -> None:
    library = tmp_path / "library"
    source = library / "incoming"
    source.mkdir(parents=True)

    with pytest.raises(PathValidationError, match="inside the managed library"):
        PathValidator().validate((ScanRoot(source),), library)


def test_library_inside_source_is_supported_for_organize_in_place(tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    library = source / "Sorted"
    library.mkdir(parents=True)

    validated = PathValidator().validate((ScanRoot(source, True),), library)

    assert validated.roots[0].path == source.resolve()
    assert validated.library_root == library.resolve()


def test_overlapping_sources_are_rejected(tmp_path: Path) -> None:
    parent = tmp_path / "incoming" / "parent"
    child = parent / "child"
    library = tmp_path / "library"
    child.mkdir(parents=True)
    library.mkdir()

    with pytest.raises(PathValidationError, match="overlap"):
        PathValidator().validate(
            (ScanRoot(parent), ScanRoot(child)),
            library,
        )
