<#
.SYNOPSIS
  Teste de carga (PROMPT 13): banco dati_load próprio, API 8200 e gateway 8201; 500 coletores com
  WebSocket aberto, leituras de 20.000 equipamentos por hora e tela de parque abaixo de 1 s.
.EXAMPLE
  scripts\load.ps1
  scripts\load.ps1 -Agents 500 -Devices 20000 -Hours 3
#>
param([int]$Agents = 500, [int]$Devices = 20000, [int]$Hours = 2, [switch]$KeepDb)
. "$PSScriptRoot\common.ps1"
Assert-Venv
$extra = @()
if ($KeepDb) { $extra += '--keep-db' }
& $VenvPython (Join-Path $PSScriptRoot 'load.py') --agents $Agents --devices $Devices --hours $Hours @extra
if ($LASTEXITCODE -ne 0) { Stop-WithError "teste de carga falhou (código $LASTEXITCODE)" }
Write-Ok "teste de carga passou ($Devices equipamentos, $Agents coletores)"
