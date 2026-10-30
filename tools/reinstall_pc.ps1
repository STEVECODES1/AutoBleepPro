# Rebuilds this PC's AutoBleep setup after a fresh Windows (10-28: the PC was
# reset; D:\AutoBleepPro-git survived, everything on C: did not).
#
# Safe to run again: every step skips what is already there. Writes what it
# does to logs\reinstall.log. Never asks for or types a password.
#
#   powershell -ExecutionPolicy Bypass -File tools\reinstall_pc.ps1
#
# Run it OUTSIDE the Claude app's own sandbox (a scheduled task or a normal
# window): installers started from inside a packaged app write to a
# private copy of AppData that msiexec cannot see (Python failed 0x80070003).
param([switch]$SkipModels)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$log = Join-Path $root "logs\reinstall.log"
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say($m) { $line = "[$(Get-Date -Format 'HH:mm:ss')] $m"; $line; Add-Content -Path $log -Value $line }
Say "=== reinstall start ($env:USERNAME on $env:COMPUTERNAME) ==="

$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) {
    $setup = "$env:USERPROFILE\Downloads\python-3.12.10-amd64.exe"
    if (Test-Path $setup) {
        Say "installing Python 3.12"
        Start-Process $setup -ArgumentList '/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=0','Include_test=0' -Wait
    }
}
if (-not (Test-Path $py)) { Say "ERROR: Python 3.12 is missing - cannot continue"; exit 2 }
Say "python: $(& $py --version 2>&1)"

# The rest of the session must see what was just installed.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")

# Not called "winget": PowerShell would resolve the call inside to itself.
function Install-Pkg($id, $exe, $extra = @()) {
    if ($exe -and (Get-Command $exe -ErrorAction SilentlyContinue)) { Say "$id already there"; return }
    Say "winget install $id"
    $wargs = @('install', '-e', '--id', $id, '--silent', '--accept-package-agreements',
               '--accept-source-agreements', '--disable-interactivity') + $extra
    & winget.exe @wargs 2>&1 | Select-Object -Last 3 | ForEach-Object { Say "  $_" }
}
Install-Pkg "Git.Git" "git" @('--scope', 'user')
Install-Pkg "Gyan.FFmpeg" "ffmpeg"
Install-Pkg "DenoLand.Deno" "deno"
if (-not (Test-Path "C:\Program Files\Google\Chrome\Application\chrome.exe")) {
    Install-Pkg "Google.Chrome" ""
}
# Needs an admin "Yes" on screen; everything else works without it.
if (-not (Test-Path "$env:WINDIR\System32\msvcp140.dll")) {
    Install-Pkg "Microsoft.VCRedist.2015+.x64" ""
}
$env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")

Say "pip: upgrading pip"
& $py -m pip install --upgrade pip 2>&1 | Select-Object -Last 1 | ForEach-Object { Say "  $_" }
Say "pip: uploader requirements"
& $py -m pip install -r "$root\auto_uploader\requirements.txt" 2>&1 | Select-Object -Last 2 | ForEach-Object { Say "  $_" }
Say "pip: AutoReel requirements"
& $py -m pip install -r "$root\requirements.txt" 2>&1 | Select-Object -Last 2 | ForEach-Object { Say "  $_" }
# GPU: torch with CUDA (openai-whisper, demucs), and the cuBLAS/cuDNN that
# faster-whisper's CTranslate2 loads on the RTX 4060.
$cuda = & $py -c "import torch,sys; sys.stdout.write(str(torch.cuda.is_available()))" 2>$null
if ($cuda -ne "True") {
    Say "pip: torch with CUDA 12.4"
    & $py -m pip install --force-reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu124 2>&1 | Select-Object -Last 2 | ForEach-Object { Say "  $_" }
}
Say "pip: CUDA libraries for faster-whisper"
& $py -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10" 2>&1 | Select-Object -Last 1 | ForEach-Object { Say "  $_" }
& $py -m pip install --upgrade "yt-dlp[default,curl-cffi]" 2>&1 | Select-Object -Last 1 | ForEach-Object { Say "  $_" }

# The 10-20 crash loop: present-but-broken installs. Import every critical
# package and force-reinstall what does not import (utils/deps.py does the
# same at every start, this is the first pass).
Say "checking every package imports"
& $py -X utf8 "$root\tools\check_packages.py" --repair 2>&1 | ForEach-Object { Say "  $_" }

New-Item -ItemType Directory -Force "C:\AutoBleep\recording" | Out-Null
Say "C:\AutoBleep\recording ready"

if (-not $SkipModels) {
    Say "downloading the Whisper model (large-v3-turbo)"
    & $py -X utf8 -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cpu', compute_type='int8'); print('model ok')" 2>&1 | Select-Object -Last 1 | ForEach-Object { Say "  $_" }
    & $py -X utf8 -c "import whisper; whisper.load_model('turbo', device='cpu'); print('openai-whisper model ok')" 2>&1 | Select-Object -Last 1 | ForEach-Object { Say "  $_" }
}

# At logon: one task, 60 s after sign-in, starts everything once (it never
# starts a second copy). A task rather than the Startup folder: it does not
# depend on which app wrote it.
$ps = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$action = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\_AUTOSTART.ps1`" -DelaySeconds 60" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName "AutoBleepPro Autostart" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Say "logon task 'AutoBleepPro Autostart' registered"

# The daily checks that were Windows tasks before the reset.
function Daily($name, $at, $script, $extra) {
    $a = New-ScheduledTaskAction -Execute $py -Argument "-X utf8 `"$root\tools\$script`" $extra" -WorkingDirectory $root
    $t = New-ScheduledTaskTrigger -Daily -At $at
    Register-ScheduledTask -TaskName $name -Action $a -Trigger $t -Settings $settings -Force | Out-Null
    Say "daily task '$name' at $at"
}
Daily "AutoBleepPro Fan Guard" "09:05" "channel_guard.py" ""
Daily "AutoBleepPro Disclaimer Backfill" "09:30" "disclaimer_backfill.py" "--apply --max 10 --only games,compilations"
# Weekly copy of the Chrome profile to D: (10-30: a corrupt cache crashed every page).
$bk = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\tools\backup_chrome_profile.ps1`"" -WorkingDirectory $root
$bt = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "05:10"
Register-ScheduledTask -TaskName "AutoBleepPro Chrome Backup" -Action $bk -Trigger $bt -Settings $settings -Force | Out-Null
Say "weekly task 'AutoBleepPro Chrome Backup' (Sunday 05:10)"

Say "=== reinstall done ==="
