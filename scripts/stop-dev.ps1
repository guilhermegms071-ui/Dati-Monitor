<#
.SYNOPSIS
  Encerra o ambiente de desenvolvimento iniciado pelo scripts\dev.ps1 (útil quando a janela foi fechada
  sem Ctrl+C, ou em testes automatizados).
#>
. "$PSScriptRoot\common.ps1"
$n = Stop-DevProcesses
if ($n -gt 0) { Write-Ok "$n processo(s) do dev.ps1 encerrado(s)" } else { Write-Ok 'Nenhum processo do dev.ps1 em execução' }
