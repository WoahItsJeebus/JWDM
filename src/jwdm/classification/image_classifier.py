"""Bounded, read-only raster metadata classification."""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Final

from PIL import Image, UnidentifiedImageError

from jwdm.classification.extension_classifier import IMAGE_EXTENSIONS
from jwdm.pipeline.models import Classification, ClassificationDisposition


_PILLOW_EXTENSIONS: Final[frozenset[str]] = IMAGE_EXTENSIONS - frozenset({".svg"})
_PHOTO_EXIF_TAGS: Final[frozenset[int]] = frozenset(
    {
        271,  # Make
        272,  # Model
        306,  # DateTime
        34853,  # GPSInfo
        36867,  # DateTimeOriginal
        36868,  # DateTimeDigitized
    }
)
_ICON_NAME: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[._\-\s])(avatar|favicon|icon|logo)(?:$|[._\-\s])",
    re.IGNORECASE,
)


class ImageMetadataClassifier:
    """Use image headers and EXIF without decoding full pixel data."""

    def classify(self, path: Path) -> Classification | None:
        extension = path.suffix.casefold()
        if extension not in _PILLOW_EXTENSIONS:
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    width, height = image.size
                    format_name = image.format or extension.lstrip(".").upper()
                    frames = int(getattr(image, "n_frames", 1))
                    exif_tags = frozenset(image.getexif().keys())
        except FileNotFoundError:
            return None
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
        ) as error:
            return Classification(
                category="Images",
                confidence="low",
                reason=(
                    "Image metadata could not be safely read; the broad Images category "
                    f"is only a suggestion ({type(error).__name__})"
                ),
                disposition=ClassificationDisposition.REVIEW,
            )

        dimensions = f"{width}x{height}"
        if frames > 1:
            return Classification(
                category="Images/Animated",
                confidence="high",
                reason=(
                    f"Image metadata: {format_name}, {dimensions}, {frames} frames "
                    "-> Images/Animated"
                ),
            )
        if exif_tags.intersection(_PHOTO_EXIF_TAGS):
            return Classification(
                category="Images/Photos",
                confidence="high",
                reason=(
                    f"Image metadata: {format_name}, {dimensions}, photo EXIF "
                    "-> Images/Photos"
                ),
            )
        if extension == ".ico" or (
            width == height and width <= 512 and _ICON_NAME.search(path.stem)
        ):
            return Classification(
                category="Images/Icons",
                confidence="high",
                reason=(
                    f"Image metadata: {format_name}, {dimensions}, icon signal "
                    "-> Images/Icons"
                ),
            )
        return Classification(
            category="Images",
            confidence="high",
            reason=f"Image metadata: {format_name}, {dimensions} -> Images",
        )
