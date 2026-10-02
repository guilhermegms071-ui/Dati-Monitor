<#
.SYNOPSIS
  Restaura um backup do scripts\backup.ps1 num banco (recriado) e, com -RestoreFiles, os arquivos.
.DESCRIPTION
  Usa o superusuário do .env para recriar o banco (dono = papel da aplicação, fuso UTC) e restaura com
  pg_restore. Banco de destino com tabelas só é sobrescrito com -Force (e sem conexões abertas: pare o
  scripts\dev.ps1 / os serviços antes). No fim confere a revisão das migrações e as linhas de cada tabela
  contra o manifesto do backup.
.EXAMPLE
  scripts\restore.ps1 -Manifest var\backups\dati-dati_dev-20261002-130000.json -Database dati_restaurado
  scripts\restore.ps1 -Manifest ...json -Database dati_dev -Force -RestoreFiles
#>
param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$Database,
    [switch]$Force,
    [switch]$RestoreFiles
)
. "$PSScriptRoot\common.ps1"

if ($Database -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { Stop-WithError "Nome de banco inválido: $Database" }
if (-not (Test-Path $Manifest)) { Stop-WithError "Manifesto não encontrado: $Manifest" }
$info = Get-Content $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
$dir = Split-Path (Resolve-Path $Manifest) -Parent
$dump = Join-Path $dir $info.dump
if ((Get-FileHash -Algorithm SHA256 $dump).Hash.ToLower() -ne $info.dump_sha256) { Stop-WithError "sha256 do dump não confere: $dump" }

$envVals = Import-DotEnv
$superUser = Get-RequiredEnv $envVals 'POSTGRES_SUPERUSER'
$superPass = Get-RequiredEnv $envVals 'POSTGRES_SUPERUSER_PASSWORD'
$pgHost = Get-RequiredEnv $envVals 'POSTGRES_HOST'
$pgPort = Get-RequiredEnv $envVals 'POSTGRES_PORT'
$appUser = Get-RequiredEnv $envVals 'DB_APP_USER'
$pgBin = Get-PgBin
$psql = Join-Path $pgBin 'psql.exe'

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$What) {
    $ErrorActionPreference = 'Continue'
    $out = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { Stop-WithError "$What falhou (código $code): $($out -join ' ')" }
    return $out
}
function Invoke-Sql([string]$Db, [string]$Sql, [string]$What) {
    return Invoke-Native $psql @('-h', $pgHost, '-p', $pgPort, '-U', $superUser, '-d', $Db, '-v', 'ON_ERROR_STOP=1', '-At', '-c', $Sql) $What
}

$env:PGPASSWORD = $superPass
try {
    $exists = (Invoke-Sql 'postgres' "SELECT 1 FROM pg_database WHERE datname = '$Database'" 'consulta do banco') -eq '1'
    if ($exists) {
        $tables = [int](Invoke-Sql $Database "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'" 'consulta de tabelas')
        if ($tables -gt 0 -and -not $Force) {
            Stop-WithError "O banco $Database já tem $tables tabela(s). Use -Force para substituí-lo (os dados atuais serão perdidos)."
        }
        $conns = [int](Invoke-Sql 'postgres' "SELECT count(*) FROM pg_stat_activity WHERE datname = '$Database'" 'consulta de conexões')
        if ($conns -gt 0) { Stop-WithError "Há $conns conexão(ões) abertas em ${Database}: pare o dev.ps1/serviços antes de restaurar." }
        Invoke-Sql 'postgres' "DROP DATABASE $Database" 'DROP DATABASE' | Out-Null
    }
    Invoke-Sql 'postgres' "CREATE DATABASE $Database OWNER $appUser ENCODING 'UTF8' TEMPLATE template0" 'CREATE DATABASE' | Out-Null
    Invoke-Sql $Database "ALTER DATABASE $Database SET timezone TO 'UTC'" 'fuso do banco' | Out-Null
    Write-Step "Restaurando $($info.dump) em $Database"
    Invoke-Native (Join-Path $pgBin 'pg_restore.exe') @('-h', $pgHost, '-p', $pgPort, '-U', $superUser, '-d', $Database, '--no-owner', "--role=$appUser", '--exit-on-error', $dump) 'pg_restore' | Out-Null

    $revision = (Invoke-Sql $Database 'SELECT version_num FROM alembic_version' 'revisão').Trim()
    if ($revision -ne $info.alembic_revision) { Stop-WithError "revisão restaurada ($revision) difere do backup ($($info.alembic_revision))" }
    $bad = 0
    foreach ($p in $info.row_counts.PSObject.Properties) {
        $n = [int64](Invoke-Sql $Database "SELECT count(*) FROM public.`"$($p.Name)`"" "contagem de $($p.Name)")
        $lo = [int64]$p.Value[0]; $hi = [int64]$p.Value[1]
        if ($n -lt $lo -or $n -gt $hi) { Write-Fail "$($p.Name): $n linha(s), o backup tinha entre $lo e $hi"; $bad++ }
    }
    if ($bad) { Stop-WithError "$bad tabela(s) com contagem diferente do backup" }
    Write-Ok "banco restaurado: revisão $revision, $(@($info.row_counts.PSObject.Properties).Count) tabelas conferidas"
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

if ($RestoreFiles) {
    if (-not $info.files) { Stop-WithError 'Este backup não tem arquivos.' }
    $zip = Join-Path $dir $info.files
    if ((Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower() -ne $info.files_sha256) { Stop-WithError "sha256 dos arquivos não confere: $zip" }
    $storage = if ($envVals.ContainsKey('STORAGE_DIR') -and $envVals['STORAGE_DIR']) { $envVals['STORAGE_DIR'] } else { Join-Path $RepoRoot 'var\storage' }
    if ((Test-Path $storage) -and (Get-ChildItem $storage -Force | Select-Object -First 1) -and -not $Force) {
        Stop-WithError "A pasta $storage não está vazia. Use -Force para substituir."
    }
    Expand-Archive -Path $zip -DestinationPath $storage -Force
    Write-Ok "arquivos restaurados em $storage"
}
Write-Ok 'Restauração concluída'
