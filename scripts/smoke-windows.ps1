#Requires -Version 5.1
<#
.SYNOPSIS
  Smoke-test Harness Windows branch after install. Run on Windows only.

.EXAMPLE
  cd $env:USERPROFILE\.local\share\harness-agent
  .\scripts\smoke-windows.ps1
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Error "Missing venv at $Py — run scripts/install.ps1 first"
}

Write-Host "=== unittest (registry + prompt) ==="
& $Py -m unittest tests.test_windows_branch -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== PowerShell tool ==="
$psTest = @'
from jarvis.tools.windows.powershell import run_powershell
out = run_powershell("Write-Output harness-smoke-ok")
assert "harness-smoke-ok" in out, out
print("run_powershell OK:", out.strip())
'@
& $Py -c $psTest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Clipboard ==="
$clipTest = @'
from jarvis.tools.windows.clipboard import clipboard_set, clipboard_get
clipboard_set("harness-clip-test")
assert clipboard_get() == "harness-clip-test"
print("clipboard OK")
'@
& $Py -c $clipTest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== UI Automation probe ==="
$uiTest = @'
from jarvis.tools.windows.ui import check_permissions, frontmost_app
print(check_permissions())
print("frontmost:", frontmost_app())
'@
& $Py -c $uiTest

Write-Host "`n=== OCR probe (sample skipped if no test image) ==="
$ocrTest = @'
import tempfile, pathlib
from jarvis.tools.ocr import read_image_text
# minimal 1x1 png
png = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
p = pathlib.Path(tempfile.gettempdir()) / "harness_smoke.png"
p.write_bytes(png)
out = read_image_text(str(p))
print("OCR result:", out[:200])
'@
& $Py -c $ocrTest

Write-Host "`nAll smoke checks finished."
