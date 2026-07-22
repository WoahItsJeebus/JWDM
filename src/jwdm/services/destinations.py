"""Safe category and collision destination handling."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath


class CategoryValidationError(ValueError):
    """A proposed category cannot be represented as a safe relative path."""


_INVALID_WINDOWS_CHARACTERS = re.compile(r'[<>:"\\|?*]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def validate_category(category: str) -> str:
    """Return a normalized slash-delimited category or raise an explicit error."""

    normalized_input = category.strip().replace("\\", "/")
    relative = PurePosixPath(normalized_input)
    if not normalized_input or relative.is_absolute():
        raise CategoryValidationError("Category must be a non-empty relative path.")

    clean_parts: list[str] = []
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise CategoryValidationError("Category cannot contain empty, dot, or parent segments.")
        if part.endswith((" ", ".")) or _INVALID_WINDOWS_CHARACTERS.search(part):
            raise CategoryValidationError(f"Category contains an invalid Windows folder name: {part}")
        if part.split(".", maxsplit=1)[0].upper() in _RESERVED_WINDOWS_NAMES:
            raise CategoryValidationError(f"Category uses a reserved Windows name: {part}")
        clean_parts.append(part)
    return "/".join(clean_parts)


def destination_for(library_root: Path, category: str, filename: str) -> Path:
    safe_category = validate_category(category)
    destination = library_root.joinpath(*safe_category.split("/"), filename)
    resolved_parent = destination.parent.resolve(strict=False)
    resolved_library = library_root.resolve(strict=True)
    if not resolved_parent.is_relative_to(resolved_library):
        raise CategoryValidationError("Destination escapes the managed library.")
    return destination


def resolve_collision(base_destination: Path, reserved: set[str] | None = None) -> tuple[Path, str]:
    """Choose a currently unused keep-both path without creating it."""

    reserved_paths = reserved if reserved is not None else set()
    counter = 0
    while True:
        if counter == 0:
            candidate = base_destination
        else:
            candidate = base_destination.with_name(
                f"{base_destination.stem} ({counter}){base_destination.suffix}"
            )
        identity = os.path.normcase(str(candidate.resolve(strict=False)))
        if not candidate.exists() and identity not in reserved_paths:
            reserved_paths.add(identity)
            behavior = "none" if counter == 0 else "numbered_keep_both"
            return candidate, behavior
        counter += 1

