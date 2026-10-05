<#
.SYNOPSIS
  Aceitação final (PROMPT seção 15): sobe tudo do zero, roda todas as suítes (lint, Go, pytest, Vitest,
  Playwright, instalador, caos com 60 min de queda, soak de 30 min, carga) e imprime OK/FALHOU por critério.
.DESCRIPTION
  Leva cerca de 3 horas. Como ADMINISTRADOR também instala de verdade os serviços (critério 15) e o caos
  reinicia o serviço do PostgreSQL. Relatório em var\acceptance\<data>\relatorio.md.
.EXAMPLE
  scripts\acceptance.ps1
  scripts\acceptance.ps1 -Skip soak,load      # rodada rápida (os itens dessas etapas ficam FALHOU)
#>
param([string]$Skip = '', [double]$SoakMinutes = 30, [double]$OutageMinutes = 60)
. "$PSScriptRoot\common.ps1"
Assert-Venv
$extra = @('--soak-minutes', $SoakMinutes, '--outage-minutes', $OutageMinutes)
if ($Skip) { $extra += @('--skip', $Skip) }
& $VenvPython (Join-Path $PSScriptRoot 'acceptance.py') @extra
if ($LASTEXITCODE -ne 0) { Stop-WithError "aceitação com critério(s) FALHOU (código $LASTEXITCODE): veja o relatório" }
Write-Ok 'aceitação: todos os critérios da seção 15 OK'
