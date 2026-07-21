[CmdletBinding()]
param(
  [string]$Origin,
  [string]$PairingCode,
  [string]$DeviceName = $env:COMPUTERNAME
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$helperRoot = $PSScriptRoot
$launcher = Join-Path $helperRoot 'printvault-helper.pyz'
$configPath = Join-Path $helperRoot 'config.json'

function Get-SlicerChoice {
  $choices = @(
    @{ Id = 'orca'; Label = 'OrcaSlicer'; Path = 'C:\Program Files\OrcaSlicer\orca-slicer.exe'; Winget = 'SoftFever.OrcaSlicer' },
    @{ Id = 'bambu'; Label = 'Bambu Studio'; Path = 'C:\Program Files\Bambu Studio\bambu-studio.exe'; Winget = 'Bambulab.BambuStudio' },
    @{ Id = 'prusa'; Label = 'PrusaSlicer'; Path = 'C:\Program Files\Prusa3D\PrusaSlicer\prusa-slicer.exe'; Winget = 'Prusa3D.PrusaSlicer' },
    @{ Id = 'cura'; Label = 'UltiMaker Cura'; Path = 'C:\Program Files\UltiMaker Cura\UltiMaker-Cura.exe'; Winget = 'Ultimaker.Cura' }
  )
  Write-Host ''
  Write-Host 'Slicer auswaehlen:'
  for ($i = 0; $i -lt $choices.Count; $i++) { Write-Host "  $($i + 1)) $($choices[$i].Label)" }
  Write-Host '  5) Anderer Slicer / eigener EXE-Pfad'
  $selection = Read-Host 'Auswahl (1-5)'
  if ($selection -match '^[1-4]$') {
    $slicer = $choices[[int]$selection - 1]
    if (-not (Test-Path $slicer.Path)) {
      $install = Read-Host "$($slicer.Label) fehlt. Jetzt mit winget installieren? [J/n]"
      if ($install -notmatch '^[nN]$') {
        winget install --id $slicer.Winget --exact --accept-package-agreements --accept-source-agreements --silent
      }
    }
    if (-not (Test-Path $slicer.Path)) { $slicer.Path = Read-Host "Vollstaendiger Pfad zu $($slicer.Label)-EXE" }
    if (-not (Test-Path $slicer.Path)) { throw 'Slicer-Datei nicht gefunden.' }
    return $slicer
  }
  if ($selection -eq '5') {
    $path = Read-Host 'Vollstaendiger Pfad zur Slicer-EXE'
    if (-not (Test-Path $path)) { throw 'Slicer-Datei nicht gefunden.' }
    $label = Read-Host 'Anzeigename fuer diesen Slicer'
    if (-not $label) { $label = 'Eigener Slicer' }
    return @{ Id = 'custom'; Label = $label; Path = $path; Winget = '' }
  }
  throw 'Ungueltige Auswahl.'
}

Write-Host ''
Write-Host 'PrintVault Helper - Einrichtungsassistent' -ForegroundColor Cyan
Write-Host 'Richtet Python, Slicer, Kopplung und config.json ein.'
Write-Host ''

if (-not (Test-Path $launcher)) { throw 'printvault-helper.pyz fehlt. ZIP vollstaendig entpacken und setup-windows.bat starten.' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
  Write-Host 'Python 3.10+ fehlt. Installiere Python 3.12 via winget ...'
  winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements --silent
  throw 'Python wurde installiert. Terminal schliessen, neu oeffnen und setup-windows.bat erneut starten.'
}
if (-not $Origin) { $Origin = Read-Host 'PrintVault HTTPS-Adresse (z. B. https://printvault.example.com)' }
if (-not $PairingCode) { $PairingCode = Read-Host 'Kopplungscode aus PrintVault Einstellungen Helper' }
if (-not $DeviceName) { $DeviceName = Read-Host 'Geraetename' }
if ($Origin -notmatch '^https://[^/?#]+$') { throw 'Nur HTTPS-Origin ohne Pfad erlaubt.' }
$slicer = Get-SlicerChoice

$registration = & py -3 $launcher --register --origin $Origin --pairing-code $PairingCode --device-name $DeviceName | ConvertFrom-Json
if (-not $registration.user_id -or -not $registration.device_id -or -not $registration.device_credential) { throw 'Geraeteregistrierung fehlgeschlagen.' }

$config = [ordered]@{
  version = 1
  origin = $Origin
  user_id = $registration.user_id
  device_id = $registration.device_id
  auth = @{ type = 'bearer_env'; token_env = 'PRINTVAULT_HELPER_TOKEN' }
  profiles = @(@{ id = $slicer.Id; label = $slicer.Label; executable = $slicer.Path; args = @('{file}') })
}
$config | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $configPath
[Environment]::SetEnvironmentVariable('PRINTVAULT_HELPER_TOKEN', [string]$registration.device_credential, 'User')
Write-Host ''
Write-Host 'Fertig. Helper ist gekoppelt und config.json wurde erstellt.' -ForegroundColor Green
Write-Host "Konfiguration: $configPath"
Write-Host "Slicer: $($slicer.Label) ($($slicer.Path))"
Write-Host 'Neues Terminal oeffnen, damit der lokale Token verfuegbar ist.'
