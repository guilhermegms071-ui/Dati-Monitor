<#
.SYNOPSIS
  Backup do Dati Monitor: banco PostgreSQL (pg_dump formato custom) + arquivos enviados (STORAGE_DIR:
  walks, logs, versões e instaladores), com manifesto (sha256, revisão das migrações, linhas por tabela).
.DESCRIPTION
  Não precisa de administrador. Credenciais do .env (nunca impressas). O dump é conferido com
  pg_restore --list antes de o backup ser dado como bom. -Keep apaga os backups mais antigos.
.EXAMPLE
  scripts\backup.ps1                         # banco do DATABASE_URL, em var\backups
  scripts\backup.ps1 -Database dati_dev -OutDir D:\backups -Keep 30
#>
param(
    [string]$Database,
    [string]$OutDir,
    [int]$Keep = 14
)
. "$PSScriptRoot\common.ps1"

$envVals = Import-DotEnv
$pgHost = Get-RequiredEnv $envVals 'POSTGRES_HOST'
$pgPort = Get-RequiredEnv $envVals 'POSTGRES_PORT'
$appUser = Get-RequiredEnv $envVals 'DB_APP_USER'
$appPass = Get-RequiredEnv $envVals 'DB_APP_PASSWORD'
if (-not $Database) {
    $url = Get-RequiredEnv $envVals 'DATABASE_URL'
    $Database = ($url -split '/')[-1].Split('?')[0]
}
if ($Database -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { Stop-WithError "Nome de banco inválido: $Database" }
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'var\backups' }
$storage = if ($envVals.ContainsKey('STORAGE_DIR') -and $envVals['STORAGE_DIR']) { $envVals['STORAGE_DIR'] } else { Join-Path $RepoRoot 'var\storage' }
$pgBin = Get-PgBin
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$base = Join-Path $OutDir "dati-$Database-$stamp"
$dump = "$base.dump"
$files = "$base-arquivos.zip"
$manifest = "$base.json"

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$What) {
    $ErrorActionPreference = 'Continue'
    $out = & $Exe @Arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { Stop-WithError "$What falhou (código $code): $($out -join ' ')" }
    return $out
}

$env:PGPASSWORD = $appPass
$psql = Join-Path $pgBin 'psql.exe'
# Contagem antes e depois do dump: com o sistema no ar, o dump fica entre as duas.
$countSql = "SELECT c.relname || '=' || (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I', c.relname), false, true, '')))[1]::text " +
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition ORDER BY 1"
function Get-Counts {
    $result = @{}
    foreach ($line in (Invoke-Native $psql @('-h', $pgHost, '-p', $pgPort, '-U', $appUser, '-d', $Database, '-At', '-c', $countSql) 'contagem de linhas')) {
        $k, $v = $line -split '=', 2
        $result[$k] = [int64]$v
    }
    return $result
}
try {
    Write-Step "Backup do banco $Database"
    $before = Get-Counts
    Invoke-Native (Join-Path $pgBin 'pg_dump.exe') @('-h', $pgHost, '-p', $pgPort, '-U', $appUser, '-d', $Database, '-Fc', '-Z', '6', '-f', $dump) 'pg_dump' | Out-Null
    $after = Get-Counts
    $list = Invoke-Native (Join-Path $pgBin 'pg_restore.exe') @('--list', $dump) 'pg_restore --list (conferência do dump)'
    $tables = @($list | Where-Object { $_ -match ' TABLE DATA ' }).Count
    Write-Ok "dump conferido: $tables tabela(s) com dados ($([math]::Round((Get-Item $dump).Length / 1MB, 1)) MB)"
    $revision = (Invoke-Native $psql @('-h', $pgHost, '-p', $pgPort, '-U', $appUser, '-d', $Database, '-At', '-c', 'SELECT version_num FROM alembic_version') 'leitura da revisão').Trim()
    $counts = [ordered]@{}
    foreach ($k in ($before.Keys | Sort-Object)) {
        $a = $before[$k]
        $b = if ($after.ContainsKey($k)) { $after[$k] } else { $a }
        $counts[$k] = @([math]::Min($a, $b), [math]::Max($a, $b))
    }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

Write-Step "Arquivos de $storage"
if (Test-Path $storage) {
    Compress-Archive -Path (Join-Path $storage '*') -DestinationPath $files -CompressionLevel Optimal
    Write-Ok "arquivos: $files"
} else {
    Write-Host "AVISO: pasta de arquivos $storage não existe; backup só do banco." -ForegroundColor Yellow
    $files = $null
}

$info = [ordered]@{
    created_at       = (Get-Date).ToUniversalTime().ToString('o')
    database         = $Database
    alembic_revision = $revision
    dump             = (Split-Path $dump -Leaf)
    dump_sha256      = (Get-FileHash -Algorithm SHA256 $dump).Hash.ToLower()
    files            = if ($files) { Split-Path $files -Leaf } else { $null }
    files_sha256     = if ($files) { (Get-FileHash -Algorithm SHA256 $files).Hash.ToLower() } else { $null }
    row_counts       = $counts  # [mínimo, máximo] entre o início e o fim do dump
}
$info | ConvertTo-Json -Depth 4 | Set-Content -Path $manifest -Encoding UTF8
Write-Ok "manifesto: $manifest (revisão $revision)"

$old = Get-ChildItem $OutDir -Filter "dati-$Database-*.json" | Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($m in $old) {
    $prefix = $m.FullName.Substring(0, $m.FullName.Length - 5)
    Remove-Item "$prefix.json", "$prefix.dump", "$prefix-arquivos.zip" -ErrorAction SilentlyContinue
    Write-Ok "backup antigo removido: $($m.BaseName)"
}
Write-Ok "Backup concluído: $base.*"
