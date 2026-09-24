param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
$studioPython = Join-Path $studioRoot '.local\venv\Scripts\python.exe'
$studioLocal = Join-Path $studioRoot '.local'
$studioAddress = 'http://127.0.0.1:7862'
if (-not (Test-Path -LiteralPath $studioPython -PathType Leaf)) {
    throw 'Run scripts\install.ps1 first. This launcher never uses the novel project runtime.'
}

function Test-StudioService {
    try {
        $studioStatus = Invoke-RestMethod -Uri ($studioAddress + '/api/status') -TimeoutSec 2
    } catch { return $false }
    if ($studioStatus.app -ne 'idea-to-video') {
        throw 'Port 7862 belongs to a different service. Nothing was stopped or changed.'
    }
    return $true
}

$studioRunning = Test-StudioService
if (-not $studioRunning) {
    $studioOccupied = @(Get-NetTCPConnection -LocalPort 7862 -State Listen -ErrorAction SilentlyContinue)
    if ($studioOccupied.Count -gt 0) {
        throw 'Port 7862 is already occupied but is not a healthy idea-to-video service. Nothing was stopped or changed.'
    }
    New-Item -ItemType Directory -Force -Path $studioLocal | Out-Null
    $studioProcess = Start-Process -FilePath $studioPython -ArgumentList @('-m', 'app.server') -WorkingDirectory $studioRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $studioLocal 'studio.log') -RedirectStandardError (Join-Path $studioLocal 'studio-error.log')
    for ($studioAttempt = 0; $studioAttempt -lt 25; $studioAttempt++) {
        if (Test-StudioService) { $studioRunning = $true; break }
        if ($studioProcess.HasExited) { break }
        Start-Sleep -Seconds 1
    }
}
if (-not $studioRunning) { throw 'Studio did not become ready. Read .local\studio-error.log; no other service was changed.' }
Write-Host ('Idea to Video: ' + $studioAddress)
if (-not $NoBrowser) { Start-Process $studioAddress }
