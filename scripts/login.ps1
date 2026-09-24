$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $PSScriptRoot
$studioPython = Join-Path $studioRoot '.local\venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $studioPython -PathType Leaf)) { throw 'Run scripts\install.ps1 first.' }
& $studioPython (Join-Path $PSScriptRoot 'dreamina-login.py')
if ($LASTEXITCODE -ne 0) { throw 'Dreamina login was not completed. Existing projects were not changed.' }
