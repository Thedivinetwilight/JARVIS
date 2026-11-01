<#
.SYNOPSIS
    Remove the J.A.R.V.I.S. Windows PC companion from the current user's profile.

.DESCRIPTION
    Deletes the launcher, the private virtual environment, this client's journal and the per-app
    notification registration.

    It does **not** contact the server. A device token stays valid until the client is revoked, so the
    recommended order is: revoke first (which tells the server and deletes the credential), then
    uninstall. If the credential cannot be revoked, revoke the client from the server console.

.PARAMETER KeepState
    Keep the journal and configuration (useful when reinstalling).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1
#>
[CmdletBinding()]
param(
    [switch]$KeepState
)

$ErrorActionPreference = "Stop"
$state = Join-Path $env:LOCALAPPDATA "JARVIS\Companion"
$shim = Join-Path $state "bin\jarvis-companion.cmd"
$registration = "HKCU:\Software\Classes\AppUserModelId\JARVIS.Companion"

Write-Host "J.A.R.V.I.S. companion uninstall"

if (Test-Path $shim) {
    Write-Host "Revoking this client with the server (best effort)..."
    try { & $shim revoke } catch { Write-Warning "Could not revoke: $($_.Exception.Message). Revoke it from the server console." }
}

if (Test-Path $registration) {
    Remove-Item -Path $registration -Recurse -Force
    Write-Host "Removed the per-app notification registration."
}

if ($KeepState) {
    if (Test-Path (Join-Path $state "venv")) { Remove-Item -Recurse -Force (Join-Path $state "venv") }
    if (Test-Path (Join-Path $state "bin")) { Remove-Item -Recurse -Force (Join-Path $state "bin") }
    Write-Host "Kept the journal and configuration in $state."
} elseif (Test-Path $state) {
    Remove-Item -Recurse -Force $state
    Write-Host "Removed $state (launcher, environment, journal, configuration)."
}

Write-Host "Done. The device credential lives in Windows Credential Manager: check"
Write-Host "  Control Panel > Credential Manager > Windows Credentials > JARVIS/companion/<host>:<port>"
Write-Host "and remove it if it is still listed."
