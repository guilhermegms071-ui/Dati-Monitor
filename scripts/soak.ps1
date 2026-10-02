<#
.SYNOPSIS
  Teste de resistência do coletor (PROMPT 13): dm-agent real contra o scripts\dev.ps1 por N minutos,
  medindo memória, goroutines, handles e fila; falha se crescerem mais de 20% depois de estabilizar.
.EXAMPLE
  scripts\soak.ps1 -Minutes 30      # o do CI
  scripts\soak.ps1 -Minutes 1440    # 24 h
#>
param([double]$Minutes = 30, [string]$Api = 'http://127.0.0.1:8000')
. "$PSScriptRoot\common.ps1"
Assert-Venv
try { Invoke-RestMethod "$Api/api/health" | Out-Null } catch { Stop-WithError "API fora do ar em ${Api}: suba o scripts\dev.ps1 antes" }
& $VenvPython (Join-Path $PSScriptRoot 'soak.py') --minutes $Minutes --api $Api
if ($LASTEXITCODE -ne 0) { Stop-WithError "teste de resistência falhou (código $LASTEXITCODE)" }
Write-Ok "teste de resistência de $Minutes min passou"
