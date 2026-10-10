# Brings the whole system back after a reboot - and never starts a second copy.
#
# Why: on 2026-10-10 the PC restarted at 08:48. The recorder came back, the
# uploader and the Rumble Chrome did not, so clips, uploads and the Rumble
# live relay would all have sat dead until somebody noticed.
#
# Run at login from the Startup folder ("AutoBleep Autostart.cmd") and from
# START.bat. Each piece starts only if it is not already running, because
# two recorders or two uploaders means double recordings and double posts.
param([int]$DelaySeconds = 0)
$ErrorActionPreference = "SilentlyContinue"
if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Running($pattern) {
    [bool](Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $pattern })
}

# The logged-in Rumble browser: Rumble uploads and the live relay drive it.
if (-not (Running 'remote-debugging-port=9222')) {
    Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -WindowStyle Minimized `
        -ArgumentList '--remote-debugging-port=9222', '--user-data-dir=C:\RumbleChromeProfile',
                      '--no-first-run', '--no-default-browser-check', 'https://rumble.com/'
    "started Rumble Chrome"
}
if (-not (Running 'record_stream\.py|_RUN_RECORDER\.bat')) {
    Start-Process cmd -ArgumentList '/k', "`"$root\_RUN_RECORDER.bat`"" -WindowStyle Normal
    "started recorder"
    Start-Sleep -Seconds 3
}
if (-not (Running 'main\.py|_RUN_UPLOADER\.bat')) {
    Start-Process cmd -ArgumentList '/k', "`"$root\_RUN_UPLOADER.bat`"" -WindowStyle Minimized
    "started uploader"
}
if (-not (Running 'rumble_live\.py|_RUN_RUMBLE_LIVE\.bat')) {
    Start-Process cmd -ArgumentList '/k', "`"$root\_RUN_RUMBLE_LIVE.bat`"" -WindowStyle Minimized
    "started Rumble live relay"
}
