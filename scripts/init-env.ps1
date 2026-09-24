<#
.SYNOPSIS
  Cria o .env da raiz com segredos aleatórios (JWT, chave mestre AES, senha do papel "dati").
  Não sobrescreve um .env existente. Informe a senha do superusuário do PostgreSQL instalado.
.EXAMPLE
  scripts\init-env.ps1 -PostgresPassword 'senha-do-postgres'
#>
param([Parameter(Mandatory = $true)][string]$PostgresPassword)
. "$PSScriptRoot\common.ps1"

$path = Join-Path $RepoRoot '.env'
if (Test-Path $path) { Stop-WithError ".env já existe em $path; apague-o manualmente se quiser gerar outro." }

function New-RandomBase64([int]$Bytes) {
    $b = New-Object byte[] $Bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return [Convert]::ToBase64String($b)
}
$appPassword = (New-RandomBase64 36) -replace '[+/=]', ''
$lines = @(
    '# Gerado por scripts\init-env.ps1. NAO versionar.',
    'POSTGRES_SUPERUSER=postgres',
    "POSTGRES_SUPERUSER_PASSWORD=$PostgresPassword",
    'POSTGRES_HOST=127.0.0.1',
    'POSTGRES_PORT=5432',
    'DB_APP_USER=dati',
    "DB_APP_PASSWORD=$appPassword",
    "DATABASE_URL=postgresql+asyncpg://dati:$appPassword@127.0.0.1:5432/dati_dev",
    "TEST_DATABASE_URL=postgresql+asyncpg://dati:$appPassword@127.0.0.1:5432/dati_test",
    'APP_ENV=development',
    'LOG_LEVEL=INFO',
    "JWT_SECRET=$(New-RandomBase64 48)",
    "MASTER_KEY=$(New-RandomBase64 32)",
    'COOKIE_SECURE=false',
    'SMTP_HOST=127.0.0.1',
    'SMTP_PORT=1025',
    'PUBLIC_BASE_URL=http://localhost:5173'
)
[IO.File]::WriteAllText($path, ($lines -join "`n") + "`n")
Write-Ok ".env criado em $path. Próximo passo: scripts\setup-db.ps1"
