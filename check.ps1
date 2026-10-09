# Installs a published Windows installer on a clean machine, starts the app the way the Start menu does, and
# watches what a person would see: the window, the page in it, any console window, the log files. It never
# fails early: whatever happens is written down (out/), and the exit code says whether the app opened.
param(
  [Parameter(Mandatory = $true)] [string] $Url,          # the installer's address (a public release)
  [Parameter(Mandatory = $true)] [string] $ExeName,      # the installed program's file name, e.g. "J.A.R.V.I.S..exe"
  [string] $Expect = '',                                 # text the window's title should have once the page is up
  [string] $Account = 'runner',                          # runner (this account) | "Robert Parker" (a new account whose name has a space) | "José Pérez"
  [string] $Defender = 'default',                        # default | on (real-time scanning switched on)
  [int] $WaitSeconds = 240,
  [string] $Out = 'out'
)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$timeline = New-Object System.Collections.Generic.List[string]
$t0 = Get-Date
function Note($text) {
  $line = ('{0,7:N1}s  {1}' -f ((Get-Date) - $t0).TotalSeconds, $text)
  $timeline.Add($line); Write-Host $line
}
function Save-Timeline { $timeline | Set-Content -Encoding UTF8 (Join-Path $Out 'timeline.txt') }

# ── the machine ──
$facts = @()
$os = Get-CimInstance Win32_OperatingSystem
$facts += "OS: $($os.Caption) $($os.Version) build $($os.BuildNumber)"
$facts += "CPU: $((Get-CimInstance Win32_Processor | Select-Object -First 1).Name)"
$facts += "RAM: $([math]::Round($os.TotalVisibleMemorySize / 1MB, 1)) GB"
$facts += "Locale: $((Get-Culture).Name), user: $env:USERNAME, profile: $env:USERPROFILE"
try { $mp = Get-MpComputerStatus; $facts += "Defender: real-time=$($mp.RealTimeProtectionEnabled) antivirus=$($mp.AntivirusEnabled) tamper=$($mp.IsTamperProtected)" } catch { $facts += "Defender: not readable ($($_.Exception.Message))" }
try { $facts += "SmartScreen (Explorer): $((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer' -ErrorAction Stop).SmartScreenEnabled)" } catch { $facts += 'SmartScreen: not set' }
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$facts += "Screen: $($screen.Width)x$($screen.Height)"
if ($Defender -eq 'on') {
  try { Set-MpPreference -DisableRealtimeMonitoring $false; $facts += 'Defender real-time scanning switched on for this run' } catch { $facts += "could not switch Defender on: $($_.Exception.Message)" }
  try { $mp = Get-MpComputerStatus; $facts += "Defender now: real-time=$($mp.RealTimeProtectionEnabled)" } catch {}
}
$facts | Set-Content -Encoding UTF8 (Join-Path $Out 'machine.txt')
$facts | ForEach-Object { Write-Host $_ }

# ── the account the app is installed and run by ──
$credential = $null
$work = 'C:\Users\Public\check'
New-Item -ItemType Directory -Force -Path $work | Out-Null
if ($Account -ne 'runner') {
  $password = ConvertTo-SecureString 'Check-Only-9x!Pw' -AsPlainText -Force   # a throwaway account on a throwaway machine
  try { Remove-LocalUser -Name $Account -ErrorAction SilentlyContinue } catch {}
  New-LocalUser -Name $Account -Password $password -FullName $Account -AccountNeverExpires -PasswordNeverExpires | Out-Null
  Add-LocalGroupMember -Group 'Users' -Member $Account -ErrorAction SilentlyContinue
  $credential = New-Object System.Management.Automation.PSCredential(".\$Account", $password)
  Note "account '$Account' made"
}
function Run-As($file, $arguments) {
  $a = @{ FilePath = $file; PassThru = $true; WorkingDirectory = $work }
  if ($arguments) { $a.ArgumentList = $arguments }
  if ($credential) { $a.Credential = $credential; $a.LoadUserProfile = $true }
  Start-Process @a
}
$userHome = if ($credential) { "C:\Users\$Account" } else { $env:USERPROFILE }

# ── download ──
$setup = Join-Path $work 'Setup.exe'
curl.exe -L --fail --silent --show-error -o $setup $Url
if (-not (Test-Path $setup)) { Note 'the installer could not be downloaded'; Save-Timeline; exit 2 }
$size = (Get-Item $setup).Length
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash
Note "downloaded $([math]::Round($size / 1MB, 1)) MB, sha256 $hash"
$signature = Get-AuthenticodeSignature $setup
Note "signature of the installer: $($signature.Status)"

# ── install, silently (a per-user install: no administrator rights) ──
$install = Run-As $setup '/S'
$done = $install.WaitForExit(600000)
Note "installer finished: exit $($install.ExitCode), waited out: $done"
Start-Sleep -Seconds 3
$programs = Join-Path $userHome 'AppData\Local\Programs'
$exe = Get-ChildItem $programs -Filter $ExeName -Recurse -Depth 2 -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(Uninstall|elevate)' } | Select-Object -First 1
if (-not $exe) {
  Note "the program $ExeName was not found under $programs"
  Get-ChildItem $programs -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 40 FullName | Out-String | Add-Content (Join-Path $Out 'timeline.txt')
  Save-Timeline; exit 3
}
$dir = $exe.DirectoryName
Note "installed: $($exe.FullName)"
$files = Get-ChildItem $dir -Recurse -File -ErrorAction SilentlyContinue
Note "files installed: $($files.Count), $([math]::Round(($files | Measure-Object Length -Sum).Sum / 1MB)) MB"
Get-ChildItem $userHome\Desktop, "$userHome\AppData\Roaming\Microsoft\Windows\Start Menu\Programs" -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object { Note "shortcut: $($_.FullName)" }

# ── start it, and watch ──
$port = 9333
$started = Get-Date
$app = Run-As $exe.FullName "--remote-debugging-port=$port"
Note "started (pid $($app.Id))"
$opened = $false; $sawWindow = $false; $sawPage = $false; $sawConsole = $false; $sawPython = $false
$lastTitle = $null; $pageUrl = ''; $pageTitle = ''
$deadline = $started.AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
  $mine = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) })
  if ($mine.Count -eq 0 -and $app.HasExited) { Note "the app is no longer running (exit $($app.ExitCode))"; break }
  $python = @($mine | Where-Object { $_.Name -eq 'python' })
  if ($python.Count -gt 0 -and -not $sawPython) { $sawPython = $true; Note "the engine (python) started: pid $($python[0].Id)" }
  foreach ($p in $python) { if ($p.MainWindowHandle -ne 0 -and -not $sawConsole) { $sawConsole = $true; Note "A CONSOLE WINDOW IS SHOWING for the engine: '$($p.MainWindowTitle)'" } }
  $main = $mine | Where-Object { $_.MainWindowHandle -ne 0 -and $_.Name -ne 'python' } | Select-Object -First 1
  if ($main) {
    if (-not $sawWindow) { $sawWindow = $true; Note "a window is showing: '$($main.MainWindowTitle)'" }
    if ($main.MainWindowTitle -ne $lastTitle) { $lastTitle = $main.MainWindowTitle; Note "window title: '$lastTitle'" }
  }
  try {
    $targets = Invoke-RestMethod -Uri "http://127.0.0.1:$port/json" -TimeoutSec 3
    $page = $targets | Where-Object { $_.type -eq 'page' } | Select-Object -First 1
    if ($page) {
      if ($page.url -ne $pageUrl) { $pageUrl = $page.url; Note ("page: " + ($pageUrl -replace 'token=[0-9a-f]+', 'token=…')) }
      $pageTitle = $page.title
      if ($pageUrl -like 'http://127.0.0.1:*' -and -not $sawPage) { $sawPage = $true; Note "the page is loaded from the engine (title '$pageTitle')" }
    }
  } catch {}
  if ($sawPage -and $sawWindow -and ($Expect -eq '' -or $lastTitle -like "*$Expect*" -or $pageTitle -like "*$Expect*")) { $opened = $true; break }
  Start-Sleep -Seconds 2
}
Note ("verdict: " + $(if ($opened) { 'OPENED' } else { 'NOT OPEN' }))
Start-Sleep -Seconds 3

# ── what is there to see ──
$shot = Join-Path $Out 'screen.png'
$bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
$bitmap.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
Note 'screenshot taken'
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase) } |
  Select-Object Id, Name, MainWindowHandle, MainWindowTitle, StartTime, @{n = 'MB'; e = { [math]::Round($_.WorkingSet64 / 1MB) } } | Format-Table -AutoSize | Out-String | Set-Content (Join-Path $Out 'processes.txt')
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object Id, Name, MainWindowTitle | Format-Table -AutoSize | Out-String | Set-Content (Join-Path $Out 'windows.txt')
try {
  Invoke-RestMethod -Uri "http://127.0.0.1:$port/json" -TimeoutSec 3 | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $Out 'pages.json')
} catch {}
$logs = Join-Path $userHome 'AppData\Local'
Get-ChildItem $logs -Directory -ErrorAction SilentlyContinue | Where-Object { Test-Path (Join-Path $_.FullName 'Logs') } | ForEach-Object {
  $to = Join-Path $Out ("logs-" + $_.Name); New-Item -ItemType Directory -Force -Path $to | Out-Null
  Copy-Item (Join-Path $_.FullName 'Logs\*') $to -Recurse -Force -ErrorAction SilentlyContinue
}
try {
  Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = $started.AddMinutes(-1) } -ErrorAction Stop |
    Where-Object { $_.ProviderName -match 'Application Error|Windows Error Reporting|\.NET Runtime|Application Hang' } |
    Select-Object -First 30 TimeCreated, ProviderName, Message | Format-List | Out-String | Set-Content (Join-Path $Out 'crashes.txt')
} catch { 'no application errors logged' | Set-Content (Join-Path $Out 'crashes.txt') }
Save-Timeline

# ── a second start while it runs: the first one should come forward, not a second backend ──
if ($opened) {
  $second = Run-As $exe.FullName ''
  Start-Sleep -Seconds 8
  $count = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like '*\backend\python\python.exe' }).Count
  Note "after a second start: $count engine process(es), the second start has exited: $($second.HasExited)"
  Save-Timeline
}
if ($opened) { exit 0 } else { exit 1 }
