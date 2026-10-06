<#
.SYNOPSIS
  Zera o banco de desenvolvimento (dati_dev) para começar um teste limpo: apaga clientes, locais,
  coletores, equipamentos, leituras, alertas e usuários; recria as tabelas e o superadmin.
.DESCRIPTION
  - Só mexe no dati_dev (o dati_test dos testes automáticos não é tocado). Recusa APP_ENV=production.
  - Pare o dev.ps1 antes (Ctrl+C ou scripts\stop-dev.ps1): o script confere as portas.
  - Apaga também os arquivos enviados pelos coletores (STORAGE_DIR, padrão var\storage).
  - Grava DEV_SEED=false no .env: o dev.ps1 deixa de criar os clientes de exemplo. Para voltar a tê-los,
    apague essa linha do .env.
  - O superadmin (BOOTSTRAP_ADMIN_EMAIL, padrão admin@local) é recriado com senha temporária, mostrada
    no fim; ela é trocada no primeiro login.
  - Coletores já instalados em outros PCs deixam de ser reconhecidos: desinstale e instale de novo com
    uma chave nova.
.EXAMPLE
  scripts\reset-dev-db.ps1           # pede para digitar ZERAR
  scripts\reset-dev-db.ps1 -Force    # sem pergunta
#>
param([switch]$Force)
. "$PSScriptRoot\common.ps1"
Assert-Venv
$envVals = Import-DotEnv
if ($envVals['APP_ENV'] -eq 'production') { Stop-WithError 'APP_ENV=production: este script é só para desenvolvimento.' }
$superUser = Get-RequiredEnv $envVals 'POSTGRES_SUPERUSER'
$superPass = Get-RequiredEnv $envVals 'POSTGRES_SUPERUSER_PASSWORD'
$pgHost = Get-RequiredEnv $envVals 'POSTGRES_HOST'
$pgPort = Get-RequiredEnv $envVals 'POSTGRES_PORT'
$appUser = Get-RequiredEnv $envVals 'DB_APP_USER'
$dbUrl = Get-RequiredEnv $envVals 'DATABASE_URL'
$db = 'dati_dev'
if ($dbUrl -notmatch "/$db(\?|$)") { Stop-WithError "DATABASE_URL não aponta para $db; por segurança, nada foi apagado: $dbUrl" }

foreach ($port in 8000, 8001) {
    $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        $procId = @($busy)[0].OwningProcess
        Stop-WithError "Porta $port em uso (PID $procId): pare o dev.ps1 antes (Ctrl+C ou scripts\stop-dev.ps1)."
    }
}

if (-not $Force) {
    Write-Host "Isto APAGA todos os dados do banco $db (clientes, locais, coletores, equipamentos, leituras, alertas e usuários)." -ForegroundColor Yellow
    $typed = Read-Host 'Digite ZERAR para continuar'
    if ($typed -cne 'ZERAR') { Write-Host 'Cancelado: nada foi apagado.'; exit 1 }
}

$psql = Join-Path (Get-PgBin) 'psql.exe'
$env:PGPASSWORD = $superPass
function Invoke-Psql([string]$Database, [string]$Sql) {
    $ErrorActionPreference = 'Continue'
    $out = & $psql -h $pgHost -p $pgPort -U $superUser -d $Database -v ON_ERROR_STOP=1 -At -c $Sql 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { Stop-WithError "psql falhou em ${Database}: $out" }
    return $out
}
try {
    Write-Step "Recriando o banco $db"
    Invoke-Psql 'postgres' "DROP DATABASE IF EXISTS $db WITH (FORCE)" | Out-Null
    Invoke-Psql 'postgres' "CREATE DATABASE $db OWNER $appUser ENCODING 'UTF8' TEMPLATE template0" | Out-Null
    Invoke-Psql $db "ALTER DATABASE $db SET timezone TO 'UTC'" | Out-Null
    Invoke-Psql $db "GRANT ALL ON SCHEMA public TO $appUser; ALTER SCHEMA public OWNER TO $appUser" | Out-Null
    Write-Ok "banco $db vazio"
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

$storage = if ($envVals['STORAGE_DIR']) { $envVals['STORAGE_DIR'] } else { 'var\storage' }
if (-not [IO.Path]::IsPathRooted($storage)) { $storage = Join-Path $RepoRoot $storage }
if (Test-Path $storage) {
    Remove-Item -Recurse -Force $storage
    Write-Ok "arquivos enviados pelos coletores apagados ($storage)"
}

Set-DotEnvValues @{ DEV_SEED = 'false' }

Push-Location (Join-Path $RepoRoot 'backend')
try {
    Invoke-Checked 'Banco: migrações (alembic upgrade head)' { & $VenvPython -m app.cli migrate }
    Invoke-Checked 'Banco: superadmin, marcas e perfis' { & $VenvPython -m app.cli init }
} finally { Pop-Location }

Write-Host ''
Write-Ok 'Pronto: banco zerado. Anote a senha temporária acima e suba com scripts\dev.ps1 -Lan'
