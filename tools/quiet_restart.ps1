# Restart the uploader (the keepalive brings it back) only when it is truly
# idle: no video being processed (logs\processing.json names a live pid),
# no clip post in progress, and no ffmpeg / ffprobe / python / node child.
# 10-16: node.exe (the Playwright driver) was not counted and the marker did
# not exist, so this killed a full-stream upload at 75%. Gives up after 6 min.
param([int]$Minutes = 6)
$root = 'D:\AutoBleepPro-git\auto_uploader'
$deadline = (Get-Date).AddMinutes($Minutes)
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
    if ([int]$busy -eq 0 -and $kids.Count -eq 0 -and -not $processing -and $main) {
        foreach ($p in @($main)) { Stop-Process -Id $p.ProcessId -Force }
        "$(Get-Date -Format HH:mm:ss) stopped uploader; keepalive restarts it"
        exit 0
    }
    "$(Get-Date -Format HH:mm:ss) busy: processing=$processing in_progress=$busy children=$($kids.Count)"
    Start-Sleep 10
}
"gave up - still busy"
