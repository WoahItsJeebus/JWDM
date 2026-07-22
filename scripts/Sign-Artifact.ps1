[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$resolvedPath = (Resolve-Path -LiteralPath $Path).Path
$certificate = $env:JWDM_SIGN_CERTIFICATE_SHA1
$timestampUrl = $env:JWDM_TIMESTAMP_URL
$signToolPath = $env:JWDM_SIGNTOOL_PATH

if (-not $certificate) {
    throw "JWDM_SIGN_CERTIFICATE_SHA1 is required for release signing."
}
if (-not $timestampUrl) {
    throw "JWDM_TIMESTAMP_URL is required for RFC 3161 timestamping."
}
if (-not $signToolPath -or -not (Test-Path -LiteralPath $signToolPath -PathType Leaf)) {
    throw "JWDM_SIGNTOOL_PATH must identify signtool.exe."
}

& $signToolPath sign /sha1 $certificate /fd SHA256 /tr $timestampUrl /td SHA256 /d "JWDM" $resolvedPath
if ($LASTEXITCODE -ne 0) {
    throw "Authenticode signing failed for $resolvedPath with exit code $LASTEXITCODE."
}
& $signToolPath verify /pa /all $resolvedPath
if ($LASTEXITCODE -ne 0) {
    throw "Authenticode verification failed for $resolvedPath with exit code $LASTEXITCODE."
}
