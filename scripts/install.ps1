#Requires -Version 5.1
<#
.SYNOPSIS
  Install Harness Jarvis agent on Windows (windows branch).

.EXAMPLE
  irm https://raw.githubusercontent.com/PrajsRamteke/harness-agent/windows/scripts/install.ps1 | iex
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoUrl = if ($env:JARVIS_REPO_URL) { $env:JARVIS_REPO_URL } else { "https://github.com/PrajsRamteke/harness-agent.git" }
$Branch = if ($env:JARVIS_BRANCH) { $env:JARVIS_BRANCH } else { "windows" }
$InstallDir = if ($env:JARVIS_INSTALL_DIR) { $env:JARVIS_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".local\share\harness-agent" }
$BinDir = if ($env:JARVIS_BIN_DIR) { $env:JARVIS_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }
$JarvisLink = Join-Path $BinDir "jarvis.cmd"

function Test-PythonOk([string]$Exe) {
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
    & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
    return $LASTEXITCODE -eq 0
}

function Find-Python {
    if ($env:PYTHON -and (Test-PythonOk $env:PYTHON)) { return ,@($env:PYTHON) }
    foreach ($c in @("python3", "python")) {
        if (Test-PythonOk $c) { return ,@($c) }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($ver in @("3.12", "3.11", "3.10")) {
            & py "-$ver" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", $ver) }
        }
    }
    Write-Error "Harness requires Python 3.10+. Install from https://www.python.org/downloads/ (check 'Add to PATH')."
}

function Ensure-Git {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Error "git is required. Install Git for Windows: https://git-scm.com/download/win"
    }
}

function Ensure-Path([string]$Dir) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$Dir*") {
        [Environment]::SetEnvironmentVariable("Path", "$Dir;$userPath", "User")
        $env:Path = "$Dir;$env:Path"
        Write-Host "Added $Dir to user PATH (open a new terminal if jarvis is not found)."
    }
}

Ensure-Git
$pyParts = Find-Python
$PyExe = $pyParts[0]
$PyArg = if ($pyParts.Count -gt 1) { $pyParts[1] } else { $null }

if (Test-Path (Join-Path $InstallDir ".git")) {
    Write-Host "Updating $InstallDir"
    git -C $InstallDir fetch origin $Branch
    git -C $InstallDir checkout $Branch
    git -C $InstallDir pull --ff-only origin $Branch
} elseif (Test-Path $InstallDir) {
    Write-Error "$InstallDir exists but is not a git checkout. Set JARVIS_INSTALL_DIR or remove that folder."
} else {
    Write-Host "Cloning $RepoUrl (branch $Branch) -> $InstallDir"
    New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir -Parent) | Out-Null
    git clone --branch $Branch --depth 1 $RepoUrl $InstallDir
}

Set-Location $InstallDir
$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"

if ((Test-Path $venvPy) -and -not (Test-PythonOk $venvPy)) {
    Write-Host "Recreating .venv (Python too old)"
    Remove-Item -Recurse -Force ".venv"
}

if (-not (Test-Path $venvPy)) {
    if ($PyArg) { & $PyExe "-$PyArg" -m venv .venv }
    else { & $PyExe -m venv .venv }
}

& $venvPy -m pip install --upgrade pip setuptools wheel
& $venvPy -m pip install -e .

Write-Host "Verifying packages..."
$verify = @'
import importlib, sys
required = ("anthropic", "openai", "rich", "textual", "mcp", "pywinauto", "pyperclip")
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except ImportError:
        missing.append(name)
if missing:
    print("missing: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)
'@
& $venvPy -c $verify
if ($LASTEXITCODE -ne 0) {
    Write-Host "Retrying with requirements.txt..."
    & $venvPy -m pip install -r requirements.txt
    & $venvPy -c $verify
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$launcher = @"
@echo off
"$venvPy" -m jarvis %*
"@
Set-Content -Path $JarvisLink -Value $launcher -Encoding ASCII

Ensure-Path $BinDir

Write-Host ""
Write-Host "Harness Jarvis installed (Windows branch)."
Write-Host "Command: $JarvisLink"
Write-Host "Run from any project folder: jarvis"
Write-Host ""
Write-Host "Optional speedups:"
Write-Host "  - Everything (es.exe on PATH) for fast file search"
Write-Host "  - Tesseract OCR if Windows OCR packages fail"
Write-Host "  - ripgrep (rg) for search_code"
