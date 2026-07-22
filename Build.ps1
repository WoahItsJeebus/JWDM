[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Release,
    [switch]$NoLaunch,
    [switch]$SkipInstaller,
    [switch]$RequireSignature,
    [string]$SigningCertificateThumbprint,
    [string]$TimestampUrl = "https://timestamp.digicert.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$virtualEnvironment = Join-Path $projectRoot ".venv"
$virtualPython = Join-Path $virtualEnvironment "Scripts\python.exe"
$buildKind = if ($Release) { "release" } else { "test" }
$buildRoot = Join-Path $projectRoot "build\$buildKind"
$distributionRoot = Join-Path $projectRoot "dist\$buildKind"
$existingBuildKind = Get-Item -LiteralPath Env:\JWDM_BUILD_KIND -ErrorAction SilentlyContinue
$existingSigningCertificate = Get-Item -LiteralPath Env:\JWDM_SIGN_CERTIFICATE_SHA1 -ErrorAction SilentlyContinue
$existingTimestampUrl = Get-Item -LiteralPath Env:\JWDM_TIMESTAMP_URL -ErrorAction SilentlyContinue
$existingSignToolPath = Get-Item -LiteralPath Env:\JWDM_SIGNTOOL_PATH -ErrorAction SilentlyContinue
$expectedExecutable = Join-Path $distributionRoot "JWDM\JWDM.exe"
$installerRoot = Join-Path $projectRoot "dist\installer"
$signScript = Join-Path $projectRoot "scripts\Sign-Artifact.ps1"

function Write-Step {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "[JWDM] $Message" -ForegroundColor Cyan
}

function Resolve-SupportedPython {
    $launcher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        $launcherPython = & $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $launcherPython) {
            return ($launcherPython | Select-Object -Last 1).Trim()
        }
    }

    $pythonCommand = Get-Command "python" -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $candidateVersion = & $pythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -eq 0 -and $candidateVersion.Trim() -eq "3.12") {
            return $pythonCommand.Source
        }
    }

    throw "Python 3.12 (64-bit) is required. Install it from python.org and retry."
}

function Stop-PreviousTestExecutable {
    param([Parameter(Mandatory)][string]$ExecutablePath)

    $resolvedTarget = [System.IO.Path]::GetFullPath($ExecutablePath)
    $processes = Get-Process -Name "JWDM" -ErrorAction SilentlyContinue
    foreach ($process in $processes) {
        try {
            if ($process.Path -and [System.IO.Path]::GetFullPath($process.Path) -eq $resolvedTarget) {
                Write-Step "Stopping previously launched test executable (PID $($process.Id))"
                Stop-Process -Id $process.Id -Force
                [void]$process.WaitForExit(5000)
            }
        } catch [System.ComponentModel.Win32Exception] {
            Write-Warning "Could not inspect JWDM process $($process.Id): $($_.Exception.Message)"
        }
    }
}

function Resolve-InnoCompiler {
    $override = Get-Item -LiteralPath Env:\INNO_SETUP_COMPILER -ErrorAction SilentlyContinue
    if ($null -ne $override -and (Test-Path -LiteralPath $override.Value -PathType Leaf)) {
        return $override.Value
    }
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 7\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Resolve-SignTool {
    $override = Get-Item -LiteralPath Env:\JWDM_SIGNTOOL_PATH -ErrorAction SilentlyContinue
    if ($null -ne $override -and (Test-Path -LiteralPath $override.Value -PathType Leaf)) {
        return $override.Value
    }
    $command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path -LiteralPath $kitsRoot -PathType Container) {
        $candidate = Get-ChildItem -LiteralPath $kitsRoot -Filter "signtool.exe" -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $_.DirectoryName -match "\\x64$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($null -ne $candidate) {
            return $candidate.FullName
        }
    }
    return $null
}

function Invoke-JwdmSigning {
    param([Parameter(Mandatory)][string]$ArtifactPath)

    & $signScript -Path $ArtifactPath
    if ($LASTEXITCODE -ne 0) {
        throw "Signing script failed for $ArtifactPath with exit code $LASTEXITCODE."
    }
}

Push-Location $projectRoot
try {
    Write-Step "Using project root $projectRoot"
    $basePython = Resolve-SupportedPython
    Write-Step "Using Python $basePython"
    $baseArchitecture = & $basePython -c "import struct; print(struct.calcsize('P') * 8)"
    if ($LASTEXITCODE -ne 0 -or $baseArchitecture.Trim() -ne "64") {
        throw "JWDM requires a 64-bit Python 3.12 installation."
    }

    if (-not (Test-Path -LiteralPath $virtualPython -PathType Leaf)) {
        Write-Step "Creating local virtual environment"
        & $basePython -m venv $virtualEnvironment
        if ($LASTEXITCODE -ne 0) {
            throw "Virtual environment creation failed with exit code $LASTEXITCODE."
        }
    }

    $environmentVersion = & $virtualPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or $environmentVersion.Trim() -ne "3.12") {
        throw "The existing .venv is not Python 3.12. Remove .venv and run Build.ps1 again."
    }

    if (-not $SigningCertificateThumbprint) {
        $configuredCertificate = Get-Item -LiteralPath Env:\JWDM_SIGN_CERTIFICATE_SHA1 -ErrorAction SilentlyContinue
        if ($null -ne $configuredCertificate) {
            $SigningCertificateThumbprint = $configuredCertificate.Value
        }
    }
    if ($RequireSignature -and -not $Release) {
        throw "-RequireSignature is valid only with -Release."
    }
    $signingEnabled = $Release -and -not [string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)
    if ($RequireSignature -and -not $signingEnabled) {
        throw "-RequireSignature needs -SigningCertificateThumbprint or JWDM_SIGN_CERTIFICATE_SHA1."
    }
    if ($SkipInstaller -and -not $Release) {
        throw "-SkipInstaller is valid only with -Release."
    }

    Write-Step "Installing pinned dependencies"
    & $virtualPython -m pip install --disable-pip-version-check --requirement requirements.lock
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE."
    }
    & $virtualPython -m pip install --disable-pip-version-check --no-deps --editable .
    if ($LASTEXITCODE -ne 0) {
        throw "Local package installation failed with exit code $LASTEXITCODE."
    }
    $packageVersion = (& $virtualPython -c "from jwdm import __version__; print(__version__)" | Select-Object -Last 1).Trim()

    Write-Step "Running automated smoke tests"
    & $virtualPython -m pytest
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed with exit code $LASTEXITCODE."
    }

    Stop-PreviousTestExecutable -ExecutablePath $expectedExecutable

    if ($Clean) {
        $allBuildOutput = Join-Path $projectRoot "build"
        $allDistributionOutput = Join-Path $projectRoot "dist"
        Write-Step "Removing generated build and distribution directories"
        if (Test-Path -LiteralPath $allBuildOutput) {
            Remove-Item -LiteralPath $allBuildOutput -Recurse -Force
        }
        if (Test-Path -LiteralPath $allDistributionOutput) {
            Remove-Item -LiteralPath $allDistributionOutput -Recurse -Force
        }
    } else {
        if (Test-Path -LiteralPath $buildRoot) {
            Remove-Item -LiteralPath $buildRoot -Recurse -Force
        }
        if (Test-Path -LiteralPath $distributionRoot) {
            Remove-Item -LiteralPath $distributionRoot -Recurse -Force
        }
        if ($Release -and (Test-Path -LiteralPath $installerRoot)) {
            Remove-Item -LiteralPath $installerRoot -Recurse -Force
        }
    }

    $env:JWDM_BUILD_KIND = $buildKind
    Write-Step "Building PyInstaller $buildKind executable"
    & $virtualPython -m PyInstaller --noconfirm --clean --distpath $distributionRoot --workpath $buildRoot JWDM.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $expectedExecutable -PathType Leaf)) {
        throw "Build finished without the expected executable: $expectedExecutable"
    }
    Write-Step "Built $expectedExecutable"

    $versionInfo = (Get-Item -LiteralPath $expectedExecutable).VersionInfo
    $expectedWindowsVersion = "$packageVersion.0"
    if ($versionInfo.ProductName -ne "JWDM" -or $versionInfo.ProductVersion -ne $expectedWindowsVersion) {
        throw "JWDM.exe version metadata is invalid. Expected JWDM $expectedWindowsVersion; found $($versionInfo.ProductName) $($versionInfo.ProductVersion)."
    }
    Write-Step "Verified Windows version metadata $expectedWindowsVersion"

    $signTool = $null
    if ($signingEnabled) {
        $signTool = Resolve-SignTool
        if (-not $signTool) {
            throw "signtool.exe is required when release signing is enabled. Install a Windows SDK or set JWDM_SIGNTOOL_PATH."
        }
        $env:JWDM_SIGN_CERTIFICATE_SHA1 = $SigningCertificateThumbprint
        $env:JWDM_TIMESTAMP_URL = $TimestampUrl
        $env:JWDM_SIGNTOOL_PATH = $signTool
        Write-Step "Signing and verifying compiled executable"
        Invoke-JwdmSigning -ArtifactPath $expectedExecutable
    } elseif ($Release) {
        Write-Warning "Release artifacts are unsigned. Official releases must use -RequireSignature."
    }

    $installerPath = $null
    if ($Release -and -not $SkipInstaller) {
        $innoCompiler = Resolve-InnoCompiler
        if (-not $innoCompiler) {
            throw "Inno Setup 6 or 7 is required for -Release. Install it, set INNO_SETUP_COMPILER, or use -SkipInstaller for an application-only packaging check."
        }
        New-Item -ItemType Directory -Path $installerRoot -Force | Out-Null
        $innoArguments = @("/DMyAppVersion=$packageVersion")
        if ($signingEnabled) {
            $signDefinition = "jwdm=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$signScript`" `$f"
            $innoArguments += "/DJWDMSigningEnabled"
            $innoArguments += "/Sjwdm=$signDefinition"
        }
        $innoArguments += (Join-Path $projectRoot "installer\JWDM.iss")
        Write-Step "Building per-user Inno Setup installer"
        & $innoCompiler @innoArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup failed with exit code $LASTEXITCODE."
        }
        $installerPath = Join-Path $installerRoot "JWDM-Setup-$packageVersion-x64.exe"
        if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
            throw "Installer build finished without the expected artifact: $installerPath"
        }
        if ($signingEnabled) {
            & $signTool verify /pa /all $installerPath
            if ($LASTEXITCODE -ne 0) {
                throw "Installer signature verification failed with exit code $LASTEXITCODE."
            }
        }
        Write-Step "Built $installerPath"
    }

    if ($Release) {
        $artifacts = @($expectedExecutable)
        if ($null -ne $installerPath) {
            $artifacts += $installerPath
        }
        $hashLines = foreach ($artifact in $artifacts) {
            $hash = Get-FileHash -LiteralPath $artifact -Algorithm SHA256
            "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($artifact))"
        }
        $hashPath = Join-Path $installerRoot "SHA256SUMS.txt"
        New-Item -ItemType Directory -Path $installerRoot -Force | Out-Null
        Set-Content -LiteralPath $hashPath -Value $hashLines -Encoding UTF8
        Write-Step "Wrote release checksums to $hashPath"
    }

    if (-not $NoLaunch) {
        Write-Step "Launching compiled JWDM executable"
        $launchedProcess = Start-Process -FilePath $expectedExecutable -WorkingDirectory (Split-Path -Parent $expectedExecutable) -PassThru
        Start-Sleep -Seconds 2
        $launchedProcess.Refresh()
        if ($launchedProcess.HasExited) {
            throw "JWDM.exe exited during launch verification with code $($launchedProcess.ExitCode)."
        }
        Write-Step "JWDM.exe is running (PID $($launchedProcess.Id))"
    }
} finally {
    if ($null -ne $existingBuildKind) {
        $env:JWDM_BUILD_KIND = $existingBuildKind.Value
    } else {
        Remove-Item Env:\JWDM_BUILD_KIND -ErrorAction SilentlyContinue
    }
    if ($null -ne $existingSigningCertificate) {
        $env:JWDM_SIGN_CERTIFICATE_SHA1 = $existingSigningCertificate.Value
    } else {
        Remove-Item Env:\JWDM_SIGN_CERTIFICATE_SHA1 -ErrorAction SilentlyContinue
    }
    if ($null -ne $existingTimestampUrl) {
        $env:JWDM_TIMESTAMP_URL = $existingTimestampUrl.Value
    } else {
        Remove-Item Env:\JWDM_TIMESTAMP_URL -ErrorAction SilentlyContinue
    }
    if ($null -ne $existingSignToolPath) {
        $env:JWDM_SIGNTOOL_PATH = $existingSignToolPath.Value
    } else {
        Remove-Item Env:\JWDM_SIGNTOOL_PATH -ErrorAction SilentlyContinue
    }
    Pop-Location
}
