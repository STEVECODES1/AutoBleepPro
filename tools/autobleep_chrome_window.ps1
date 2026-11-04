# Show or hide the AutoBleep Chrome (the one Rumble/Studio work runs in).
# It lives off-screen so it never covers what you are doing (11-03).
#   SHOW_AUTOBLEEP_CHROME.bat -> moves it onto the screen (to sign in, look)
#   HIDE_AUTOBLEEP_CHROME.bat -> puts it back off-screen
param([switch]$Hide)
Add-Type @"
using System; using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int ht, bool r);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
}
"@
$ids = Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" |
    Where-Object { $_.CommandLine -match 'RumbleChromeProfile' } | ForEach-Object { $_.ProcessId }
$wins = Get-Process -Id $ids -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
if (-not $wins) { "AutoBleep Chrome is not running (the keeper starts it within a minute)."; exit 1 }
foreach ($p in $wins) {
    $h = $p.MainWindowHandle
    [W]::ShowWindow($h, 9) | Out-Null          # restore if minimized
    if ($Hide) { [W]::MoveWindow($h, -2600, 0, 1600, 1000, $true) | Out-Null; "hidden" }
    else { [W]::MoveWindow($h, 80, 40, 1600, 1000, $true) | Out-Null; [W]::SetForegroundWindow($h) | Out-Null; "shown" }
}
