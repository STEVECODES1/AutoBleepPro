# Starts the logged-in Rumble Chrome when it is not running.
#
# Why: Rumble uploads and the Rumble live relay drive this browser over its
# debugging port. It was found closed on 10-16 and 10-17 (closed by hand or
# lost with a restart), and nothing brought it back - the next upload would
# have failed. _RUN_RUMBLE_CHROME.bat runs this every minute.
#
# It looks only at chrome.exe processes, so this script's own command line
# can never make it think the browser is up.
$ErrorActionPreference = "SilentlyContinue"
$up = Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" |
    Where-Object { $_.CommandLine -match 'RumbleChromeProfile' }
# Chrome when it is installed, otherwise Edge (same flags; after the 10-28
# reset the PC had Edge only).
$chromes = @("C:\Program Files\Google\Chrome\Application\chrome.exe",
             "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
$edges = @("C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
           "C:\Program Files\Microsoft\Edge\Application\msedge.exe")
# The profile decides, not which browser happens to be installed: a profile
# Edge made (and signed in) must be reopened by Edge, or every login in it
# is lost (10-29: Chrome was reinstalled while the signed-in profile was Edge's).
$edgeProfile = (Test-Path "C:\RumbleChromeProfile\Local State") -and
    ((Get-Content "C:\RumbleChromeProfile\Local State" -Raw -ErrorAction SilentlyContinue) -match '"edge|microsoft_edge|edge_')
$order = if ($edgeProfile) { $edges + $chromes } else { $chromes + $edges }
$browser = $order | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $up -and $browser) {
    Start-Process $browser -WindowStyle Minimized `
        -ArgumentList '--remote-debugging-port=9222', '--user-data-dir=C:\RumbleChromeProfile',
                      '--no-first-run', '--no-default-browser-check', 'https://rumble.com/'
    "$(Get-Date -Format 'HH:mm:ss') started Rumble Chrome"
}
