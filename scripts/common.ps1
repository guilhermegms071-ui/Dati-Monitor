# Funções compartilhadas pelos scripts do Dati Monitor. Use com: . "$PSScriptRoot\common.ps1"
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "OK  $Message" -ForegroundColor Green }
function Write-Fail([string]$Message) { Write-Host "ERRO $Message" -ForegroundColor Red }

function Stop-WithError([string]$Message) {
    Write-Fail $Message
    throw $Message
}

function Import-DotEnv {
    $path = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $path)) { Stop-WithError ".env não encontrado em $path. Copie .env.example para .env e preencha." }
    $values = @{}
    foreach ($line in Get-Content $path -Encoding utf8) {
        if ($line -match '^\s*(#|$)') { continue }
        if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { Stop-WithError ".env: linha inválida: $line" }
        $values[$Matches[1]] = $Matches[2].Trim()
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), 'Process')
    }
    return $values
}

function Get-RequiredEnv([hashtable]$Env, [string]$Name) {
    if (-not $Env.ContainsKey($Name) -or [string]::IsNullOrWhiteSpace($Env[$Name])) { Stop-WithError ".env: variável obrigatória ausente: $Name" }
    return $Env[$Name]
}

function Get-PgBin {
    $cmd = Get-Command psql.exe -ErrorAction SilentlyContinue
    if ($cmd) { return Split-Path -Parent $cmd.Source }
    $dirs = Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'bin\psql.exe') } |
        Sort-Object { [int]($_.Name -replace '\D', '') } -Descending
    if (-not $dirs) { Stop-WithError 'PostgreSQL não encontrado. Instale com: winget install -e --id PostgreSQL.PostgreSQL.16' }
    return Join-Path $dirs[0].FullName 'bin'
}

function Assert-Venv {
    if (-not (Test-Path $VenvPython)) { Stop-WithError "venv não encontrado. Rode: py -3.12 -m venv .venv; .venv\Scripts\pip install -r backend\requirements-dev.lock; .venv\Scripts\pip install --no-deps -e backend" }
}

function Get-GoExe {
    $cmd = Get-Command go.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $default = 'C:\Program Files\Go\bin\go.exe'
    if (Test-Path $default) {
        # Ferramentas como o golangci-lint chamam "go" pelo PATH; terminais abertos antes da
        # instalação do Go não o têm.
        $env:Path = "$(Split-Path -Parent $default);$env:Path"
        return $default
    }
    Stop-WithError 'Go não encontrado. Instale com: winget install -e --id GoLang.Go'
}

function Invoke-Checked([string]$Description, [scriptblock]$Block) {
    Write-Step $Description
    & $Block
    if ($LASTEXITCODE -ne 0) { Stop-WithError "$Description falhou (código $LASTEXITCODE)" }
    Write-Ok $Description
}
