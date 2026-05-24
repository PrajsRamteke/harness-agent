"""System-level tools on Windows."""
import subprocess

from ...constants import MAX_TOOL_OUTPUT, SPECK_MAX_CHARS
from ._ps import run_ps
from ..shell import run_bash


def open_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return "ERROR: url required"
    try:
        subprocess.run(["cmd", "/c", "start", "", url], check=True, timeout=15)
        return "opened"
    except Exception as e:
        return f"ERROR: {e}"


def notify(title: str, message: str = "") -> str:
    t = (title or "").replace("'", "''")
    m = (message or "").replace("'", "''")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType=WindowsRuntime] | Out-Null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$text = $template.GetElementsByTagName('text'); "
        f"$text.Item(0).AppendChild($template.CreateTextNode('{t}')) | Out-Null; "
        f"$text.Item(1).AppendChild($template.CreateTextNode('{m}')) | Out-Null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Harness').Show($toast)"
    )
    result = run_ps(ps, timeout=10)
    if result.startswith("ERROR"):
        # fallback: msg popup
        return run_ps(f"msg * /TIME:5 '{t}: {m}'", timeout=10)
    return "notified"


def speck(text: str, voice: str = "", rate: int = 0) -> str:
    t = (text or "").strip()
    if not t:
        return "ERROR: text is empty"
    if len(t) > SPECK_MAX_CHARS:
        return f"ERROR: text exceeds {SPECK_MAX_CHARS} characters"
    escaped = t.replace("'", "''")
    rate_arg = f"-Rate {int(rate)}" if rate and rate > 0 else ""
    voice_arg = f"-Voice '{voice}'" if voice else ""
    ps = f"Add-Type -AssemblyName System.Speech; "
    ps += f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    if voice:
        ps += f"$s.SelectVoice('{voice}'); "
    if rate and rate > 0:
        ps += f"$s.Rate = [Math]::Min(10, [Math]::Max(-10, {int(rate) // 20 - 5})); "
    ps += f"$s.Speak('{escaped}')"
    result = run_ps(ps, timeout=max(30, min(600, len(t) // 2)))
    return "spoke" if not result.startswith("ERROR") else result


def win_control(action: str, value: str = "") -> str:
    """System controls. action ∈ {volume, mute, unmute, battery, wifi_on, wifi_off, sleep, lock, dark_mode, light_mode, toggle_dark}."""
    a = (action or "").lower()
    if a == "volume":
        level = int(value) if value else 50
        return run_ps(f"(New-Object -ComObject WScript.Shell).SendKeys([char]175)" * (level // 10))
    if a == "mute":
        return run_ps("(New-Object -ComObject WScript.Shell).SendKeys([char]173)")
    if a == "unmute":
        return run_ps("(New-Object -ComObject WScript.Shell).SendKeys([char]174)")
    if a == "battery":
        return run_ps(
            "Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining, BatteryStatus | Format-List"
        )
    if a == "wifi_on":
        return run_bash("netsh interface set interface name=\"Wi-Fi\" admin=enabled", 10)
    if a == "wifi_off":
        return run_bash("netsh interface set interface name=\"Wi-Fi\" admin=disabled", 10)
    if a == "sleep":
        return run_ps("Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Application]::SetSuspendState('Suspend',$false,$false)")
    if a == "lock":
        return run_ps("rundll32.exe user32.dll,LockWorkStation")
    if a == "dark_mode":
        return run_ps(
            "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
            "-Name AppsUseLightTheme -Value 0"
        )
    if a == "light_mode":
        return run_ps(
            "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize' "
            "-Name AppsUseLightTheme -Value 1"
        )
    if a == "toggle_dark":
        return run_ps(
            "$p='HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize'; "
            "$v=(Get-ItemProperty $p -Name AppsUseLightTheme).AppsUseLightTheme; "
            "Set-ItemProperty $p -Name AppsUseLightTheme -Value ([int](1-$v))"
        )
    if a == "brightness":
        return "brightness: use win_control or PowerShell Get/WmiMonitorBrightness if supported"
    return f"ERROR: unknown action {action}"[:MAX_TOOL_OUTPUT]
