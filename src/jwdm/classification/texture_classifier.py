"""Conservative offline texture-map filename signals."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

from jwdm.classification.extension_classifier import IMAGE_EXTENSIONS
from jwdm.pipeline.models import Classification


_TEXTURE_TOKEN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[._\-\s])"
    r"(albedo|ambientocclusion|ao|basecolor|base_color|diffuse|displacement|"
    r"emissive|height|metal|metallic|metalness|normal|nrm|opacity|rough|"
    r"roughness|specular)"
    r"(?:$|[._\-\s]|\d+k?$)",
    re.IGNORECASE,
)


class TextureNameClassifier:
    """Recognize common PBR map tokens without assuming a specific 3D program."""

    def classify(self, path: Path) -> Classification | None:
        if path.suffix.casefold() not in IMAGE_EXTENSIONS:
            return None
        match = _TEXTURE_TOKEN.search(path.stem)
        if match is None:
            return None
        signal = match.group(1).casefold()
        return Classification(
            category="Images/Textures",
            confidence="high",
            reason=f"Texture filename signal: {signal} -> Images/Textures",
        )
