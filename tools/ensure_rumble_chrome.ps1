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
$up = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
    Where-Object { $_.CommandLine -match 'RumbleChromeProfile' }
if (-not $up) {
    Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" -WindowStyle Minimized `
        -ArgumentList '--remote-debugging-port=9222', '--user-data-dir=C:\RumbleChromeProfile',
                      '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                      'https://rumble.com/'
    "$(Get-Date -Format 'HH:mm:ss') started Rumble Chrome"
}
