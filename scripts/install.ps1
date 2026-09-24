param(
    [string]$Python = '',
    [switch]$SkipDreamina
)
$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
$studioLocal = Join-Path $studioRoot '.local'
$studioVenv = Join-Path $studioLocal 'venv'
$studioPython = Join-Path $studioVenv 'Scripts\python.exe'

function Assert-Python([string]$Executable, [string[]]$Arguments) {
    try {
        $studioProbe = & $Executable @Arguments -c 'import sys; print(sys.executable); sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $studioProbe) { return [string]@($studioProbe)[-1] }
    } catch { }
    return $null
}

if (-not (Test-Path -LiteralPath $studioPython -PathType Leaf)) {
    $studioBase = $null
    if ($Python) {
        $studioBase = Assert-Python $Python @()
        if (-not $studioBase) { throw 'The specified Python must be Python 3.12 or later.' }
    } else {
        $studioPy = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($studioPy) { $studioBase = Assert-Python $studioPy.Source @('-3') }
        if (-not $studioBase) {
            foreach ($studioCandidate in @(Get-Command python.exe, python3.exe -ErrorAction SilentlyContinue)) {
                if ($studioCandidate.Source -notlike '*\WindowsApps\*') {
                    $studioBase = Assert-Python $studioCandidate.Source @()
                    if ($studioBase) { break }
                }
            }
        }
    }
    if (-not $studioBase) { throw 'Install Python 3.12+ from python.org or run install.ps1 -Python <full-path-to-python.exe>. No existing project was changed.' }
    New-Item -ItemType Directory -Force -Path $studioLocal | Out-Null
    & $studioBase -m venv $studioVenv
    if ($LASTEXITCODE -ne 0) { throw 'Creating the independent virtual environment failed.' }
}
if (-not (Assert-Python $studioPython @())) { throw 'The project runtime must be Python 3.12 or later.' }
& $studioPython -m pip install --disable-pip-version-check --only-binary=:all: -r (Join-Path $studioRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Re-run after restoring network access.' }
& $studioPython -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
if ($LASTEXITCODE -ne 0) { throw 'The independent FFmpeg dependency could not be verified.' }

if (-not $SkipDreamina) {
    $studioTools = Join-Path $studioRoot '.tools'
    $studioCli = Join-Path $studioTools 'dreamina.exe'
    if (-not (Test-Path -LiteralPath $studioCli -PathType Leaf)) {
        if (-not [Environment]::Is64BitOperatingSystem) { throw 'The official Dreamina download used here requires 64-bit Windows.' }
        New-Item -ItemType Directory -Force -Path $studioTools | Out-Null
        # Discovered in https://jimeng.jianying.com/cli on 2026-09-24.
        # Download the binary only; never execute the remote installer script.
        $studioDownload = 'https://lf3-static.bytednsdoc.com/obj/eden-cn/psj_hupthlyk/ljhwZthlaukjlkulzlp/dreamina_cli_beta/dreamina_cli_windows_amd64.exe'
        $studioTemporary = Join-Path $studioTools ('dreamina-' + [guid]::NewGuid().ToString('N') + '.part')
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Host 'Downloading the official Dreamina CLI into this project only...'
        Invoke-WebRequest -UseBasicParsing -Uri $studioDownload -OutFile $studioTemporary -TimeoutSec 180
        $studioFile = Get-Item -LiteralPath $studioTemporary
        if ($studioFile.Length -lt 1000000 -or $studioFile.Length -gt 300000000) {
            throw ('The downloaded file has an unexpected size; it was not executed. Inspect: ' + $studioTemporary)
        }
        $studioStream = [IO.File]::OpenRead($studioTemporary)
        try { $studioMagic = @($studioStream.ReadByte(), $studioStream.ReadByte()) } finally { $studioStream.Dispose() }
        if ($studioMagic[0] -ne 77 -or $studioMagic[1] -ne 90) { throw 'The official download did not return a Windows executable. It was not executed.' }
        # No overwrite: a concurrently installed binary is kept intact.
        Move-Item -LiteralPath $studioTemporary -Destination $studioCli
        Write-Host ('Downloaded SHA256 (local record, not a vendor signature): ' + (Get-FileHash -LiteralPath $studioCli -Algorithm SHA256).Hash)
    }
    Write-Host 'Dreamina is installed. Run scripts\login.ps1 to authorize this independent project.'
}
Write-Host 'Installation complete. Double-click Start-Studio.cmd to open http://127.0.0.1:7862/.'
