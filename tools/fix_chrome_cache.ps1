# The AutoBleep Chrome crashes on every page ("Aw, Snap!" STATUS_ACCESS_VIOLATION):
# its cache is corrupt (10-30). Close that Chrome window first, then run this.
# Moves the cache folders to _to_delete (never deletes); logins, cookies and
# extensions stay. The keeper reopens Chrome within a minute.
$prof = "C:\RumbleChromeProfile"
$dst = "D:\AutoBleepPro-git\_to_delete\chrome_cache_$(Get-Date -Format yyyyMMdd_HHmm)"
if (Get-CimInstance Win32_Process -Filter "name='chrome.exe'" | Where-Object { $_.CommandLine -match 'RumbleChromeProfile' }) {
    "The AutoBleep Chrome is still open - close its window, then run this again."; exit 1
}
New-Item -ItemType Directory -Force $dst | Out-Null
$moved = @()
foreach ($d in @("GrShaderCache","ShaderCache","GraphiteDawnCache","Default\GPUCache","Default\Code Cache",
                 "Default\Cache","Default\DawnGraphiteCache","Default\DawnWebGPUCache",
                 "Default\Service Worker\CacheStorage","Default\Service Worker\ScriptCache")) {
    $s = Join-Path $prof $d
    if (Test-Path $s) {
        Move-Item $s (Join-Path $dst ($d -replace '[\\ ]','_')) -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $s)) { $moved += $d }
    }
}
"Moved: " + ($moved -join ", ")
"Done - the keeper reopens Chrome within a minute."
