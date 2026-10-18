# Wait until the uploader is truly idle, then restart the Rumble Chrome and
# the uploader together (the keepalives bring both back). Same idle test as
# quiet_restart.ps1: no video being processed (logs\processing.json names a
# live pid), no clip post in progress, no ffmpeg / ffprobe / python / node
# child of main.py. For changes that need both restarted without ever
# cutting an upload short - a full stream's Rumble upload runs inside that
# Chrome for an hour or more.
param([int]$Hours = 8, [switch]$ChromeOnly)
$root = 'D:\AutoBleepPro-git\auto_uploader'
$deadline = (Get-Date).AddHours($Hours)
while ((Get-Date) -lt $deadline) {
    $busy = python -c "import json;j=json.load(open(r'$root\clip_jobs.json',encoding='utf-8'))['jobs'];print(sum(1 for v in j.values() if v['state']=='in_progress'))"
    $main = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'main\.py --(watch|batch)' }
    $kids = @()
    if ($main) { $kids = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -in @($main.ProcessId) -and $_.Name -in @('ffmpeg.exe','ffprobe.exe','python.exe','node.exe') } }
    $processing = $false
    $marker = "$root\logs\processing.json"
    if (Test-Path $marker) {
        try {
            $m = Get-Content $marker -Raw | ConvertFrom-Json
            if (@($main.ProcessId) -contains [int]$m.pid) { $processing = $true }
        } catch { $processing = $true }
    }
    if ([int]$busy -eq 0 -and $kids.Count -eq 0 -and -not $processing) {
        Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
            Where-Object { $_.CommandLine -match 'remote-debugging-port=9222' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Start-Sleep 4
        & powershell -ExecutionPolicy Bypass -File 'D:\AutoBleepPro-git\tools\ensure_rumble_chrome.ps1'
        "$(Get-Date -Format HH:mm:ss) restarted Rumble Chrome"
        if (-not $ChromeOnly -and $main) {
            foreach ($p in @($main)) { Stop-Process -Id $p.ProcessId -Force }
            "$(Get-Date -Format HH:mm:ss) stopped uploader; keepalive restarts it"
        }
        exit 0
    }
    Start-Sleep 60
}
"gave up after $Hours h - still busy"
