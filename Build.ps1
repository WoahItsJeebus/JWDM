[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Release,
    [switch]$NoLaunch
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
$expectedExecutable = if ($Release) {
    Join-Path $distributionRoot "JWDM.exe"
} else {
    Join-Path $distributionRoot "JWDM\JWDM.exe"
}

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

    Write-Step "Installing pinned dependencies"
    & $virtualPython -m pip install --disable-pip-version-check --requirement requirements.lock
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE."
    }
    & $virtualPython -m pip install --disable-pip-version-check --no-deps --editable .
    if ($LASTEXITCODE -ne 0) {
        throw "Local package installation failed with exit code $LASTEXITCODE."
    }

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
    Pop-Location
}
