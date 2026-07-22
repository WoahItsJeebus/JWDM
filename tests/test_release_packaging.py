from __future__ import annotations

import tomllib
from pathlib import Path

from PIL import Image

from jwdm import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_and_windows_metadata_agree() -> None:
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    version_resource = (PROJECT_ROOT / "assets" / "JWDM.version").read_text(
        encoding="utf-8"
    )
    installer = (PROJECT_ROOT / "installer" / "JWDM.iss").read_text(
        encoding="utf-8"
    )

    assert project["project"]["version"] == __version__
    version_tuple = tuple(int(part) for part in __version__.split(".")) + (0,)
    assert f"filevers={version_tuple}" in version_resource
    assert f"prodvers={version_tuple}" in version_resource
    assert f"StringStruct('ProductVersion', '{__version__}.0')" in version_resource
    assert f'#define MyAppVersion "{__version__}"' in installer


def test_packaged_icon_has_required_windows_sizes() -> None:
    with Image.open(PROJECT_ROOT / "assets" / "JWDM.ico") as icon:
        sizes = icon.info.get("sizes", set())

    assert icon.format == "ICO"
    assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes


def test_release_installer_is_per_user_onedir_and_signing_ready() -> None:
    installer = (PROJECT_ROOT / "installer" / "JWDM.iss").read_text(
        encoding="utf-8"
    )
    specification = (PROJECT_ROOT / "JWDM.spec").read_text(encoding="utf-8")
    build = (PROJECT_ROOT / "Build.ps1").read_text(encoding="utf-8")
    signer = (PROJECT_ROOT / "scripts" / "Sign-Artifact.ps1").read_text(
        encoding="utf-8"
    )

    assert "PrivilegesRequired=lowest" in installer
    assert "ArchitecturesAllowed=x64compatible" in installer
    assert r"dist\release\JWDM\*" in installer
    assert "SignedUninstaller=yes" in installer
    assert "bundle = COLLECT(" in specification
    assert "exclude_binaries=True" in specification
    assert "-RequireSignature" in build
    assert "/fd SHA256" in signer
    assert "/tr $timestampUrl /td SHA256" in signer
