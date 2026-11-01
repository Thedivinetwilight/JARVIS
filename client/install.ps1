<#
.SYNOPSIS
    Install the J.A.R.V.I.S. Windows PC companion for the current user.

.DESCRIPTION
    Everything happens inside %LOCALAPPDATA%\JARVIS\Companion: a private virtual environment, a copy
    of the package, a launcher and the per-app notification registration.

    This script does **not** use pip, does **not** contact a package index and does **not** open a
    network connection. It needs only the Python 3.10+ interpreter already on the machine.

    It does not install a service, a scheduled task or an autostart entry: whether the companion runs
    at login is an owner decision that has not been made (see client/README.md).

.PARAMETER Python
    The Python launcher to use. Defaults to `py -3`, then `python`.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$state = Join-Path $env:LOCALAPPDATA "JARVIS\Companion"
$venv = Join-Path $state "venv"
$site = Join-Path $venv "Lib\site-packages"
$shim = Join-Path $state "bin\jarvis-companion.cmd"

Write-Host "J.A.R.V.I.S. companion -> $state"

$launcher = $Python
if (-not $launcher) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $launcher = "py -3"
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $launcher = "python"
    } else {
        throw "Python 3.10+ is required and was not found on PATH."
    }
}

New-Item -ItemType Directory -Force -Path $state, (Join-Path $state "bin") | Out-Null

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating a private environment with $launcher (no pip, no network)..."
    $parts = $launcher.Split(" ")
    & $parts[0] @($parts | Select-Object -Skip 1) -m venv --without-pip $venv
}

$target = Join-Path $site "jarvis_companion"
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force -Path $site | Out-Null
Copy-Item -Recurse -Force (Join-Path $root "src\jarvis_companion") $target

@"
@echo off
"$venv\Scripts\python.exe" -m jarvis_companion %*
"@ | Set-Content -Encoding ASCII -Path $shim

Write-Host "Registering the per-app notification id (HKCU only)..."
& $shim register-toast

Write-Host ""
Write-Host "Installed. Next steps:"
Write-Host "  1. On the server console, mint a single-use code with only the scopes this client needs:"
Write-Host "       python3 -m app.jarvis pair-code --config <server config> \"
Write-Host "           --label-hint `"owner PC`" --platform pc --scopes reminders.receive,status.read"
Write-Host "  2. Pair this PC:"
Write-Host "       `"$shim`" pair --server http://<server>:7420 --code <CODE>"
Write-Host "  3. Start polling (foreground; autostart is not installed):"
Write-Host "       `"$shim`" run"
Write-Host ""
Write-Host "Toast delivery itself is unverified until the checklist in client\README.md section 7 has been run."
