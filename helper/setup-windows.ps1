[CmdletBinding()]
param(
  [string]$Origin,
  [string]$PairingCode,
  [string]$DeviceName = $env:COMPUTERNAME,
  [string]$OrcaPath = 'C:\Program Files\OrcaSlicer\orca-slicer.exe'
)

$ErrorActionPreference = 'Stop'
$helperRoot = $PSScriptRoot
$launcher = Join-Path $helperRoot 'printvault-helper.pyz'
$configPath = Join-Path $helperRoot 'config.json'

Write-Host ''
Write-Host 'PrintVault Helper – Einrichtungsassistent' -ForegroundColor Cyan
Write-Host 'Dieser Assistent richtet Python, Slicer, Kopplung und config.json ein.'
Write-Host ''

if (-not (Test-Path $launcher)) { throw 'printvault-helper.pyz fehlt. ZIP vollständig entpacken und setup-windows.ps1 aus diesem Ordner starten.' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Host 'Python 3.10+ fehlt. Installiere Python 3.12 über winget ...'
  winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements --silent
  throw 'Python wurde installiert. PowerShell schließen, neu öffnen und setup-windows.ps1 erneut starten.'
}
if (-not $Origin) { $Origin = Read-Host 'PrintVault HTTPS-Adresse (z. B. https://printvault.example.com)' }
if (-not $PairingCode) { $PairingCode = Read-Host 'Kopplungscode aus PrintVault → Einstellungen → Helper' }
if (-not $DeviceName) { $DeviceName = Read-Host 'Gerätename' }
if ($Origin -notmatch '^https://[^/?#]+$') { throw 'Nur HTTPS-Origin ohne Pfad erlaubt.' }
if (-not (Test-Path $OrcaPath)) {
  $installOrca = Read-Host 'OrcaSlicer fehlt. Jetzt automatisch installieren? [J/n]'
  if ($installOrca -notmatch '^[nN]$') {
    winget install --id SoftFever.OrcaSlicer --exact --accept-package-agreements --accept-source-agreements --silent
  }
}
if (-not (Test-Path $OrcaPath)) { $OrcaPath = Read-Host 'Vollständiger Pfad zu OrcaSlicer.exe' }
if (-not (Test-Path $OrcaPath)) { throw 'Slicer-Datei nicht gefunden.' }

$registration = & py -3 $launcher --register --origin $Origin --pairing-code $PairingCode --device-name $DeviceName | ConvertFrom-Json
if (-not $registration.user_id -or -not $registration.device_id -or -not $registration.device_credential) { throw 'Geräteregistrierung fehlgeschlagen.' }

$config = [ordered]@{
  version = 1
  origin = $Origin
  user_id = $registration.user_id
  device_id = $registration.device_id
  auth = @{ type = 'bearer_env'; token_env = 'PRINTVAULT_HELPER_TOKEN' }
  profiles = @(@{ id = 'orca'; label = 'OrcaSlicer'; executable = $OrcaPath; args = @('{file}') })
}
$config | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $configPath
[Environment]::SetEnvironmentVariable('PRINTVAULT_HELPER_TOKEN', [string]$registration.device_credential, 'User')
Write-Host 'Fertig. Helper ist gekoppelt und config.json wurde erstellt.'
Write-Host "Konfiguration: $configPath"
Write-Host "Slicer: $OrcaPath"
Write-Host 'Neue PowerShell öffnen, damit der lokale Token verfügbar ist.'
