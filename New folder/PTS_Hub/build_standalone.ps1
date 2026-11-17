<#
.SYNOPSIS
    Build PTS Hub as a fast, self-contained Nuitka standalone application with external assets.

.DESCRIPTION
    Compiles main.py into a Nuitka --standalone folder (NOT --onefile, to avoid slow %TEMP%
    self-extraction, ProcessPoolExecutor overhead, and SentinelOne dropper heuristics).
    Keeps assets/, logs/, and cache/ alongside PTS_Hub.exe so all JSON configurations remain
    externally editable.

.EXAMPLE
    .\build_standalone.ps1
    .\build_standalone.ps1 -PreserveExistingAssets
    .\build_standalone.ps1 -Compiler msvc -SignThumbprint "0123456789ABCDEF..."
#>
[CmdletBinding()]
param(
    [string]$OutputDir = "dist\PTS_Hub",
    [ValidateSet("auto", "msvc", "mingw64")]
    [string]$Compiler = "auto",
    [switch]$PreserveExistingAssets,
    [switch]$EnableLTO,
    [string]$CompanyName = "PTS Engineering",
    [string]$ProductName = "PTS Hub",
    [string]$FileVersion = "1.3.0.0",
    [string]$FileDescription = "PTS Hub - FTDC Checker and Engineering Suite",
    [string]$SignThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$ResolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir
} else {
    Join-Path $ProjectRoot $OutputDir
}

$BuildWorkDir = Join-Path $ProjectRoot "build_nuitka"
$AssetsSrc = Join-Path $ProjectRoot "assets"
$IconPath = Join-Path $AssetsSrc "FTDC_Checker_icon.ico"

if (-not (Test-Path $IconPath)) {
    throw "Required icon file not found: $IconPath"
}

Write-Host "=== PTS Hub Nuitka Standalone Build ===" -ForegroundColor Cyan
python -m nuitka --version
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is not available in the active Python environment."
}

if (Test-Path $BuildWorkDir) {
    Remove-Item -Recurse -Force $BuildWorkDir
}
New-Item -ItemType Directory -Path $BuildWorkDir | Out-Null

# Choose LTO mode: disabled by default on MinGW64 for cleaner PE sections & fewer EDR heuristics
$UseLtoFlag = if ($EnableLTO) { "--lto=yes" } else { "--lto=no" }

$NuitkaArgs = @(
    "-m", "nuitka",
    "--standalone",
    "--assume-yes-for-downloads",
    "--enable-plugins=tk-inter,no-qt",
    "--disable-plugins=upx",
    "--include-package-data=sv_ttk",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=$IconPath",
    "--company-name=$CompanyName",
    "--product-name=$ProductName",
    "--file-version=$FileVersion",
    "--product-version=$FileVersion",
    "--file-description=$FileDescription",
    $UseLtoFlag,
    "--nofollow-import-to=numpy",
    "--nofollow-import-to=pandas",
    "--nofollow-import-to=openpyxl",
    "--nofollow-import-to=pytest",
    "--nofollow-import-to=tests",
    "--nofollow-import-to=*.tests",
    "--noinclude-pytest-mode=nofollow",
    "--noinclude-setuptools-mode=nofollow",
    "--noinclude-unittest-mode=nofollow",
    "--output-dir=$BuildWorkDir",
    "--output-filename=PTS_Hub.exe",
    "--remove-output"
)

if ($Compiler -eq "msvc") {
    $NuitkaArgs += "--msvc=latest"
} elseif ($Compiler -eq "mingw64") {
    $NuitkaArgs += "--mingw64"
}

$NuitkaArgs += "main.py"

Write-Host "`nRunning Nuitka compiler (standalone folder mode, NO UPX, NO onefile)..." -ForegroundColor Yellow
& python @NuitkaArgs
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka compilation failed with exit code $LASTEXITCODE."
}

$DistSrc = Join-Path $BuildWorkDir "main.dist"
$BuiltExe = Join-Path $DistSrc "PTS_Hub.exe"
if (-not (Test-Path $BuiltExe)) {
    throw "Expected compiled executable not found at: $BuiltExe"
}

# Deploy to $ResolvedOutputDir while optionally preserving user-edited JSONs, logs, and cache
if (-not (Test-Path $ResolvedOutputDir)) {
    New-Item -ItemType Directory -Path $ResolvedOutputDir -Force | Out-Null
} else {
    # Remove old binaries/runtime files cleanly while keeping assets/logs/cache intact
    Get-ChildItem -Path $ResolvedOutputDir -Force | Where-Object {
        $_.Name -notin @("assets", "logs", "cache")
    } | Remove-Item -Recurse -Force
}

Write-Host "`nStaging standalone files into: $ResolvedOutputDir" -ForegroundColor Cyan
Copy-Item -Path (Join-Path $DistSrc "*") -Destination $ResolvedOutputDir -Recurse -Force

# Stage external configurable assets beside PTS_Hub.exe
$AssetsDest = Join-Path $ResolvedOutputDir "assets"
if (-not (Test-Path $AssetsDest)) {
    New-Item -ItemType Directory -Path $AssetsDest -Force | Out-Null
}

Get-ChildItem -Path $AssetsSrc -File | ForEach-Object {
    $DestFile = Join-Path $AssetsDest $_.Name
    if ($PreserveExistingAssets -and (Test-Path $DestFile)) {
        Write-Host "  [Preserved] assets\$($_.Name)" -ForegroundColor DarkGray
    } else {
        Copy-Item -Path $_.FullName -Destination $DestFile -Force
        Write-Host "  [Copied]    assets\$($_.Name)" -ForegroundColor Green
    }
}

# Ensure runtime directories exist beside PTS_Hub.exe
foreach ($RuntimeDir in @("logs", "cache")) {
    $DirPath = Join-Path $ResolvedOutputDir $RuntimeDir
    if (-not (Test-Path $DirPath)) {
        New-Item -ItemType Directory -Path $DirPath -Force | Out-Null
    }
}

# Clean temporary Nuitka staging folder
Remove-Item -Recurse -Force $BuildWorkDir -ErrorAction SilentlyContinue

$FinalExe = Join-Path $ResolvedOutputDir "PTS_Hub.exe"

# Optional Authenticode signing when a certificate thumbprint is supplied
if (-not [string]::IsNullOrWhiteSpace($SignThumbprint)) {
    $SignToolCmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if (-not $SignToolCmd) {
        $SdkCandidates = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        if ($SdkCandidates) {
            $SignToolCmd = $SdkCandidates[0]
        }
    }
    if (-not $SignToolCmd) {
        throw "signtool.exe not found. Install Windows SDK Signing Tools or sign PTS_Hub.exe manually."
    }

    Write-Host "`nSigning PTS_Hub.exe with certificate thumbprint $SignThumbprint..." -ForegroundColor Cyan
    & $SignToolCmd.Source sign /fd SHA256 /sha1 $SignThumbprint /tr $TimestampUrl /td SHA256 $FinalExe
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticode signing failed with exit code $LASTEXITCODE."
    }
}

$Sha1 = (Get-FileHash -Path $FinalExe -Algorithm SHA1).Hash
$Sha256 = (Get-FileHash -Path $FinalExe -Algorithm SHA256).Hash

Write-Host "`n=== Build Complete ===" -ForegroundColor Green
Write-Host "Executable : $FinalExe"
Write-Host "Assets Dir : $AssetsDest"
Write-Host "SHA-1      : $Sha1"
Write-Host "SHA-256    : $Sha256"
