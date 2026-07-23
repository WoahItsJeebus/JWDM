from __future__ import annotations

from pathlib import Path

from jwdm.classification.extension_classifier import ExtensionClassifier


def test_known_extensions_use_conservative_general_categories() -> None:
    classifier = ExtensionClassifier()

    assert classifier.classify(Path("scene.BLEND")).category == "Blender/Projects"
    assert classifier.classify(Path("chair.fbx")).category == "3D Models"
    assert classifier.classify(Path("photo.JPG")).category == "Images"
    assert classifier.classify(Path("package.zip")).category == "Archives"
    assert classifier.classify(Path("place.RBXL")).category == "Roblox"
    assert classifier.classify(Path("model.rbxm")).category == "Roblox"
    assert classifier.classify(Path("trollstore.tipa")).category == "Installers/TrollStore"
    assert classifier.classify(Path("game.CT")).category == "Cheat Engine/Tables"
    assert classifier.classify(Path("sound.opus")).category == "Audio"


def test_unknown_extension_requires_review() -> None:
    result = ExtensionClassifier().classify(Path("download.custom-format"))

    assert result.category is None
    assert result.confidence == "unknown"
    assert "No built-in rule" in result.reason
