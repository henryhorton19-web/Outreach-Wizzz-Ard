# Windows PowerShell launcher for Outreach Wizz-ard (Desktop App + Auto Git Sync)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if (Test-Path ".\venv\Scripts\Activate.ps1") {
    . .\venv\Scripts\Activate.ps1
} elseif (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . .\.venv\Scripts\Activate.ps1
}

python run_synced.py $args
