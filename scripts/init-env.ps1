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
    'PUBLIC_BASE_URL=http://localhost:5173',
    'PUBLIC_SERVER_URL=http://127.0.0.1:8000',
    'PUBLIC_WS_URL=ws://127.0.0.1:8001/ws/agent'
)
[IO.File]::WriteAllText($path, ($lines -join "`n") + "`n")
# Segredos: só este usuário, o SISTEMA e os administradores leem o .env (a pasta costuma liberar leitura a
# todos os usuários do Windows). Reverter: icacls .env /reset
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $path /inheritance:r /grant:r "${me}:(F)" '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-WithError "não foi possível restringir as permissões de $path (icacls)" }
Write-Ok ".env criado em $path (leitura só deste usuário). Próximo passo: scripts\setup-db.ps1"
