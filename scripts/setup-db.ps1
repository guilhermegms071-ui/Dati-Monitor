<#
.SYNOPSIS
  Cria (de forma idempotente) o papel da aplicação e os bancos dati_dev e dati_test no PostgreSQL local.
  Não precisa de administrador. Lê as credenciais do .env da raiz.
#>
. "$PSScriptRoot\common.ps1"

$envVals = Import-DotEnv
$superUser = Get-RequiredEnv $envVals 'POSTGRES_SUPERUSER'
$superPass = Get-RequiredEnv $envVals 'POSTGRES_SUPERUSER_PASSWORD'
$pgHost = Get-RequiredEnv $envVals 'POSTGRES_HOST'
$pgPort = Get-RequiredEnv $envVals 'POSTGRES_PORT'
$appUser = Get-RequiredEnv $envVals 'DB_APP_USER'
$appPass = Get-RequiredEnv $envVals 'DB_APP_PASSWORD'
if ($appUser -notmatch '^[a-z_][a-z0-9_]*$') { Stop-WithError "DB_APP_USER inválido: $appUser" }
if ($appPass -match "'") { Stop-WithError 'DB_APP_PASSWORD não pode conter aspas simples' }

$psql = Join-Path (Get-PgBin) 'psql.exe'
$env:PGPASSWORD = $superPass

function Invoke-Psql([string]$Database, [string]$Sql) {
    # PowerShell 5.1: com ErrorAction Stop, o stderr do psql viraria exceção antes da mensagem clara abaixo.
    $ErrorActionPreference = 'Continue'
    $out = & $psql -h $pgHost -p $pgPort -U $superUser -d $Database -v ON_ERROR_STOP=1 -At -c $Sql 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { Stop-WithError "psql falhou em ${Database}: $out" }
    return $out
}

try {
    Write-Step "Conectando em ${pgHost}:${pgPort} como $superUser"
    $version = Invoke-Psql 'postgres' 'SHOW server_version'
    Write-Ok "PostgreSQL $version"

    $exists = Invoke-Psql 'postgres' "SELECT 1 FROM pg_roles WHERE rolname = '$appUser'"
    if ($exists -eq '1') {
        Invoke-Psql 'postgres' "ALTER ROLE $appUser WITH LOGIN PASSWORD '$appPass'" | Out-Null
        Write-Ok "Papel $appUser já existia (senha sincronizada com o .env)"
    } else {
        Invoke-Psql 'postgres' "CREATE ROLE $appUser WITH LOGIN PASSWORD '$appPass'" | Out-Null
        Write-Ok "Papel $appUser criado"
    }

    foreach ($db in 'dati_dev', 'dati_test') {
        $dbExists = Invoke-Psql 'postgres' "SELECT 1 FROM pg_database WHERE datname = '$db'"
        if ($dbExists -eq '1') {
            Write-Ok "Banco $db já existe"
        } else {
            Invoke-Psql 'postgres' "CREATE DATABASE $db OWNER $appUser ENCODING 'UTF8' TEMPLATE template0" | Out-Null
            Write-Ok "Banco $db criado"
        }
        Invoke-Psql $db "ALTER DATABASE $db SET timezone TO 'UTC'" | Out-Null
        Invoke-Psql $db "GRANT ALL ON SCHEMA public TO $appUser; ALTER SCHEMA public OWNER TO $appUser" | Out-Null
    }
    Write-Ok 'Bancos prontos: dati_dev e dati_test (fuso UTC)'
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
