# Weekly copy of the AutoBleep Chrome profile to D: (10-30), so a corrupt
# profile can be put back without signing in to everything again. Caches are
# skipped. Note: the saved logins are tied to this Windows user - a copy helps
# after a corrupt profile, not after a Windows reset.
$src = "C:\RumbleChromeProfile"
$dst = "D:\AutoBleepPro-git\_backups\RumbleChromeProfile"
if (-not (Test-Path $src)) { "No profile at $src"; exit 1 }
New-Item -ItemType Directory -Force $dst | Out-Null
robocopy $src $dst /MIR /R:1 /W:1 /NFL /NDL /NP /XJ `
  /XD "Cache" "Code Cache" "GPUCache" "GrShaderCache" "ShaderCache" "GraphiteDawnCache" `
      "DawnGraphiteCache" "DawnWebGPUCache" "CacheStorage" "ScriptCache" "Crashpad" "Safe Browsing" | Select-Object -Last 8
"Backed up to $dst at $(Get-Date)"
exit 0
