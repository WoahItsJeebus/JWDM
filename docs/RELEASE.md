# JWDM release and update policy

## Release format

JWDM 1.0 uses a PyInstaller **onedir** application wrapped by an Inno Setup
installer. The installer targets the current user at
`%LOCALAPPDATA%\Programs\JWDM`, creates Start Menu integration, offers an
optional desktop shortcut, and does not request administrator privileges.

The onedir format is intentional: installed files start in place rather than
extracting a one-file bundle into a temporary directory on every launch. The
portable `dist\release\JWDM\` directory remains useful for local packaging
checks, but the installer is the supported end-user distribution.

Prerequisites for a local release build:

- 64-bit CPython 3.12
- Inno Setup 6 or 7 (`ISCC.exe`), either discoverable automatically or provided
  through `INNO_SETUP_COMPILER`
- A Windows SDK `signtool.exe` only when signing is enabled

Inno Setup's current licensing terms ask commercial users to purchase a
license. Resolve that requirement before any commercial JWDM distribution.

Build an unsigned local installer check with:

```powershell
.\Build.ps1 -Release -NoLaunch
```

If Inno Setup is unavailable, an application-only diagnostic build can use
`-SkipInstaller`. That output is not a complete release.

Expected artifacts:

```text
dist\release\JWDM\JWDM.exe
dist\installer\JWDM-Setup-1.0.0-x64.exe
dist\installer\SHA256SUMS.txt
```

## Versioning and Windows metadata

JWDM uses semantic product versions. A release version must agree in:

- `src/jwdm/__init__.py`
- `pyproject.toml`
- `assets/JWDM.version` (four-part Windows file/product version)
- the fallback `MyAppVersion` in `installer/JWDM.iss`

`Build.ps1` rejects a compiled executable whose Windows product name or version
resource does not match the Python package version.

## Signed release strategy

Official public releases must be Authenticode-signed with a code-signing
certificate controlled by the project owner. Private keys, certificate files,
PINs, and service credentials must never be committed to this repository.
Prefer a hardware-backed key or a managed signing service usable from protected
release automation.

The release command is:

```powershell
.\Build.ps1 -Release -NoLaunch -RequireSignature `
  -SigningCertificateThumbprint "CERTIFICATE-SHA1-THUMBPRINT"
```

The signing path uses SHA-256 file digests and an RFC 3161 SHA-256 timestamp.
Override `-TimestampUrl` only with the certificate authority's supported
timestamp service. The application executable, installer, and generated
uninstaller are signed; signatures are verified before the build succeeds.

An unsigned `-Release` build is permitted only as a local packaging test and is
clearly labeled by the build warning. It must not be published as an official
JWDM release.

## Update strategy

JWDM 1.0 intentionally has no background self-updater. Releases are published
as versioned GitHub Release assets after tests, compiled launch verification,
installer validation, signature verification, and checksum generation.

For 1.x, users update by downloading and running the newer signed installer.
Inno Setup keeps the stable `AppId`, upgrades the existing per-user install, and
preserves `%LOCALAPPDATA%\JWDM` settings, history, logs, and relocation records
because those files live outside the install directory.

A future update checker may notify users that a release exists, but it must:

1. Use an authenticated release endpoint and compare semantic versions.
2. Never send filenames, paths, settings, or file contents.
3. Require explicit user approval before downloading or launching an installer.
4. Download to an application-owned temporary path without replacing the
   running executable directly.
5. Verify the expected SHA-256 digest and a valid JWDM Authenticode publisher
   signature before launch.
6. Refuse unsigned, mismatched, downgraded, or unverifiable artifacts.
7. Leave the current installation intact when any check fails.

No updater should be enabled until the project has a stable signing identity and
a published, authenticated release-manifest format.

## Publication checklist

1. Choose and add the repository license before public distribution.
2. Update all version sources and release notes.
3. Run the complete tests and `Build.ps1` compiled launch check.
4. Build with `-Release -RequireSignature` on a protected release machine.
5. Install, launch, upgrade, and uninstall on supported Windows 10 and Windows 11
   test systems.
6. Verify Authenticode signatures and `SHA256SUMS.txt`.
7. Tag the exact source commit and publish only its signed artifacts.
