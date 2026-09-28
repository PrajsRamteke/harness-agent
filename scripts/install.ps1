# Jarvis installer for Windows (PowerShell 5.1+). The Windows twin of scripts/install.
#
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/PrajsRamteke/harness-agent/main/scripts/install.ps1 | iex"
#
# Rerun it to update. Same knobs as scripts/install:
#   JARVIS_REPO_URL, JARVIS_BRANCH, JARVIS_INSTALL_DIR, JARVIS_BIN_DIR, PYTHON

# Everything runs inside a script block so `irm | iex` doesn't leave variables
# or functions behind in the caller's session, and a `throw` stops the install
# without closing the window.
& {
    $RepoUrl    = if ($env:JARVIS_REPO_URL)    { $env:JARVIS_REPO_URL }    else { 'https://github.com/PrajsRamteke/harness-agent.git' }
    $Branch     = if ($env:JARVIS_BRANCH)      { $env:JARVIS_BRANCH }      else { 'main' }
    $InstallDir = if ($env:JARVIS_INSTALL_DIR) { $env:JARVIS_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA 'harness-agent' }
    $BinDir     = if ($env:JARVIS_BIN_DIR)     { $env:JARVIS_BIN_DIR }     else { Join-Path $HOME '.local\bin' }

    $VenvDir = Join-Path $InstallDir '.venv'
    $VenvPy  = Join-Path $VenvDir 'Scripts\python.exe'
    $Marker  = 'rem Jarvis launcher (scripts/install.ps1)'

    # $ErrorActionPreference does not cover native commands, so check each one.
    function Assert-Exit([string]$what) {
        if ($LASTEXITCODE -ne 0) { throw "error: $what failed (exit code $LASTEXITCODE)" }
    }

    # Prints the interpreter's real path when it is Python 3.10+, nothing otherwise.
    # Also skips the Microsoft Store `python` stub, which exits non-zero.
    function Test-Python([string]$exe, [string[]]$pre = @()) {
        $ErrorActionPreference = 'Continue'   # PS 5.1 turns redirected stderr into errors
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $null }
        $out = & $exe @pre -c 'import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return "$out".Trim() }
        return $null
    }

    function Find-Python {
        if ($env:PYTHON) {
            $found = Test-Python $env:PYTHON
            if ($found) { return $found }
            throw "error: PYTHON=$($env:PYTHON) is not Python 3.10 or newer"
        }
        # `py -3` is the Windows launcher's newest installed Python 3.
        $found = Test-Python 'py' @('-3')
        if ($found) { return $found }
        foreach ($name in 'python', 'python3') {
            $found = Test-Python $name
            if ($found) { return $found }
        }
        throw "error: Jarvis requires Python 3.10 or newer`n  install one, for example: winget install -e --id Python.Python.3.12`n  then open a new terminal and rerun this installer"
    }

    function Test-Install {
        $script = @'
import importlib
import sys

required = ("anthropic", "openai", "httpx", "rich", "textual", "mcp")
missing = []
for name in required:
    try:
        importlib.import_module(name)
    except ImportError:
        missing.append(name)

if missing:
    print("missing packages: " + ", ".join(missing), file=sys.stderr)
    raise SystemExit(1)

# Import the app itself so a dependency it imports directly but pip didn't
# install fails here, not on the user's first `jarvis`.
for name in ("jarvis.tui.app", "jarvis.main"):
    try:
        importlib.import_module(name)
    except Exception as e:
        print(f"jarvis failed to import ({name}): {type(e).__name__}: {e}", file=sys.stderr)
        raise SystemExit(1)
'@
        $file = Join-Path ([IO.Path]::GetTempPath()) "jarvis-verify-$PID.py"
        Set-Content -Path $file -Value $script -Encoding ASCII -ErrorAction Stop
        try {
            & $VenvPy $file | Out-Host
            return ($LASTEXITCODE -eq 0)
        } finally {
            Remove-Item $file -ErrorAction SilentlyContinue
        }
    }

    # Prepends $dir to the user PATH (kept as REG_EXPAND_SZ so entries like
    # %USERPROFILE%\... stay unexpanded). Returns $true if PATH changed.
    function Add-UserPath([string]$dir) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
        try {
            $raw = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            $parts = @($raw -split ';' | Where-Object { $_ })
            $want = $dir.TrimEnd('\')
            foreach ($p in $parts) {
                if ([Environment]::ExpandEnvironmentVariables($p).TrimEnd('\') -ieq $want) { return $false }
            }
            $key.SetValue('Path', ((@($dir) + $parts) -join ';'), [Microsoft.Win32.RegistryValueKind]::ExpandString)
        } finally {
            $key.Close()
        }
        # Setting any user variable broadcasts WM_SETTINGCHANGE, so terminals
        # opened from now on pick up the new PATH without signing out.
        [Environment]::SetEnvironmentVariable('JARVIS_INSTALLER_TMP', '1', 'User')
        [Environment]::SetEnvironmentVariable('JARVIS_INSTALLER_TMP', $null, 'User')
        return $true
    }

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "error: missing required command: git`n  install it, for example: winget install -e --id Git.Git`n  then open a new terminal and rerun this installer"
    }
    $Python = Find-Python
    Write-Host "using Python: $Python"

    if (Test-Path (Join-Path $InstallDir '.git')) {
        Write-Host "updating $InstallDir"
        git -C $InstallDir fetch origin $Branch;          Assert-Exit 'git fetch'
        git -C $InstallDir checkout $Branch;              Assert-Exit 'git checkout'
        git -C $InstallDir pull --ff-only origin $Branch; Assert-Exit 'git pull'
    } elseif (Test-Path $InstallDir) {
        throw "error: $InstallDir exists but is not a git checkout`n  set JARVIS_INSTALL_DIR to another path or move that folder first"
    } else {
        Write-Host "cloning $RepoUrl -> $InstallDir"
        New-Item -ItemType Directory -Force -Path (Split-Path $InstallDir) -ErrorAction Stop | Out-Null
        git clone --branch $Branch --depth 1 $RepoUrl $InstallDir; Assert-Exit 'git clone'
    }

    if ((Test-Path $VenvPy) -and -not (Test-Python $VenvPy)) {
        Write-Host 'recreating .venv because it uses Python older than 3.10'
        Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop
    }
    if (-not (Test-Path $VenvPy)) {
        & $Python -m venv --upgrade-deps $VenvDir; Assert-Exit 'creating the virtual environment'
    }

    & $VenvPy -m pip install --upgrade pip setuptools wheel; Assert-Exit 'upgrading pip'
    & $VenvPy -m pip install -e $InstallDir;                 Assert-Exit 'pip install'

    Write-Host 'verifying Python packages...'
    if (-not (Test-Install)) {
        Write-Host 'retrying with requirements.txt...'
        & $VenvPy -m pip install -r (Join-Path $InstallDir 'requirements.txt'); Assert-Exit 'pip install -r requirements.txt'
        if (-not (Test-Install)) { throw 'error: Jarvis installed but failed to import (see above)' }
    }

    # A .cmd shim instead of a symlink: symlinks need admin or Developer Mode.
    New-Item -ItemType Directory -Force -Path $BinDir -ErrorAction Stop | Out-Null
    $Shim = Join-Path $BinDir 'jarvis.cmd'
    if ((Test-Path $Shim) -and -not (Select-String -Path $Shim -SimpleMatch $Marker -Quiet)) {
        throw "error: $Shim already exists and was not created by this installer`n  move it first, then rerun this installer"
    }
    # Write %LOCALAPPDATA% literally when we can: .cmd files are read in the
    # console code page, which would garble a non-ASCII user name.
    $Target = Join-Path $VenvDir 'Scripts\jarvis.exe'
    if ($Target.StartsWith($env:LOCALAPPDATA, [StringComparison]::OrdinalIgnoreCase)) {
        $Target = '%LOCALAPPDATA%' + $Target.Substring($env:LOCALAPPDATA.Length)
    }
    Set-Content -Path $Shim -Value "@echo off`r`n$Marker`r`n`"$Target`" %*" -Encoding ASCII -ErrorAction Stop

    Write-Host ''
    Write-Host 'Jarvis installed.'
    Write-Host "Command: $Shim"

    $addedPath = Add-UserPath $BinDir
    if (-not (($env:Path -split ';') -contains $BinDir)) {
        $env:Path = "$BinDir;$env:Path"
    }
    if ($addedPath) {
        Write-Host ''
        Write-Host "Added $BinDir to your user PATH."
        Write-Host 'Open a new terminal, go to any project folder and run: jarvis'
    } else {
        Write-Host 'Run from any project folder: jarvis'
    }
}
