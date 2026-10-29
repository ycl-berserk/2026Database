# Build pgvector against the pinned EDB PostgreSQL headers and MSVC.
# The source and build tree live in an ASCII path so nmake never has to deal
# with non-ASCII project paths on Windows.
param(
    [Parameter(Mandatory = $true)][string]$PgRoot,
    [string]$Version = "0.8.0",
    [string]$Sha256 = "6951d2b333f28b3c439ac5aa612bd3ec922529fc1c557a84338cc6b99e04437e",
    [string]$WorkDir = "$env:LOCALAPPDATA\LearningSpaceBuild\pgvector",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$PgRoot = (Resolve-Path $PgRoot).Path
$WorkDir = [System.IO.Path]::GetFullPath($WorkDir)
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$archive = Join-Path $WorkDir "pgvector-v$Version.zip"
if (-not (Test-Path $archive)) {
    Write-Host "[pgvector] downloading v$Version"
    Invoke-WebRequest -Uri "https://codeload.github.com/pgvector/pgvector/zip/refs/tags/v$Version" -OutFile $archive
}
$actual = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
if ($actual -ne $Sha256) {
    Remove-Item $archive -Force
    throw "pgvector archive SHA-256 mismatch: $actual"
}

$source = Join-Path $WorkDir "pgvector-$Version"
if (Test-Path $source) { Remove-Item $source -Recurse -Force }
Expand-Archive -Path $archive -DestinationPath $WorkDir -Force

$pgLink = Join-Path $WorkDir "pgsql"
if (Test-Path $pgLink) { Remove-Item $pgLink -Force }
New-Item -ItemType Junction -Path $pgLink -Target $PgRoot | Out-Null

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found; install Visual Studio Build Tools with C++." }
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw "No Visual Studio C++ toolchain found." }
$vcvars = Join-Path $vsPath "VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found under $vsPath" }

$batch = Join-Path $WorkDir "build.bat"
@"
@echo off
call "$vcvars" >nul
if errorlevel 1 exit /b 1
cd /d "$source"
nmake /nologo /F Makefile.win PGROOT="$pgLink" install
"@ | Set-Content -Path $batch -Encoding ascii

& cmd.exe /c $batch
if ($LASTEXITCODE -ne 0) { throw "pgvector build failed with exit code $LASTEXITCODE" }

if ($InstallDir) {
    $InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "lib") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "share\extension") | Out-Null
    Copy-Item (Join-Path $source "vector.dll") (Join-Path $InstallDir "lib")
    Copy-Item (Join-Path $source "vector.control") (Join-Path $InstallDir "share\extension")
    Copy-Item (Join-Path $source "sql\vector--*.sql") (Join-Path $InstallDir "share\extension")
    Write-Host "[pgvector] installed into $InstallDir"
} else {
    Write-Host "[pgvector] installed into $pgLink"
}
