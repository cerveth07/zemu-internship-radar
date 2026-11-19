param([ValidateSet('boss', 'shixiseng')][string]$Platform = 'boss')
$ErrorActionPreference = 'Stop'
$port = if ($Platform -eq 'boss') { 9333 } else { 9334 }
$profileName = if ($Platform -eq 'boss') { 'BossHunterChrome' } else { 'ShixisengChrome' }
$startUrl = if ($Platform -eq 'boss') { 'https://www.zhipin.com' } else { 'https://www.shixiseng.com' }
$profile = Join-Path $env:LOCALAPPDATA "InternshipRadarPrivateRC\$profileName"
$browserCandidates = @(
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
)
if ($Platform -eq 'shixiseng') {
    $browserCandidates += @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
    )
}
$browser = $browserCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw 'Google Chrome / Microsoft Edge is not installed.' }
$listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if ($listener) {
    $owned = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -in @('chrome.exe', 'msedge.exe') -and $_.CommandLine -and
        $_.CommandLine.Contains("--remote-debugging-port=$port") -and $_.CommandLine.Contains($profile)
    }
    if (-not $owned) { throw "Port $port is occupied by another process. Close that process before starting Zemu collection." }
    Write-Output "Dedicated $Platform browser is already running."
    exit 0
}
Start-Process -FilePath $browser -ArgumentList @(
    '--remote-debugging-address=127.0.0.1', "--remote-debugging-port=$port",
    ('--user-data-dir="' + $profile + '"'), '--no-first-run', '--no-default-browser-check', $startUrl
)
Write-Output "Dedicated $Platform browser started. Log in manually if prompted."
