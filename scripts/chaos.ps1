<#
.SYNOPSIS
  Teste de caos (PROMPT seção 13): mata o coletor, derruba API e gateway (queda de internet), derruba o
  PostgreSQL e o PC do MASTER do cluster, e verifica que nenhuma leitura se perde, não há duplicidade e
  tudo volta sozinho. Relatório OK/FALHOU por item em var\chaoselatorio.json.
.DESCRIPTION
  Usa um ambiente próprio (API 8100, gateway 8101, worker, simuladores 12161-12168) e dois dm-agent reais
  vigiados por dm-watchdog. Num terminal de administrador o passo do banco reinicia o serviço
  postgresql-x64-16; sem administrador, derruba todas as conexões do banco (pg_terminate_backend).
.EXAMPLE
  scripts\chaos.ps1                     # queda de 10 min, como pede o PROMPT
  scripts\chaos.ps1 -OutageMinutes 3    # ensaio rápido
#>
param([double]$OutageMinutes = 10)
. "$PSScriptRoot\common.ps1"
Assert-Venv
$ErrorActionPreference = 'Continue'
& $VenvPython (Join-Path $RepoRoot 'scripts\chaos.py') --outage-minutes $OutageMinutes
$code = $LASTEXITCODE
if ($code -ne 0) { Write-Fail "Teste de caos com falhas (código $code). Veja var\chaoselatorio.json e var\chaos\logs." }
else { Write-Ok 'Teste de caos: tudo voltou sozinho' }
exit $code
