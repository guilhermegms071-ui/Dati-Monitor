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
    # Sucesso/falha vem do código de saída do programa. O stderr (onde Alembic, pytest etc. escrevem
    # logs) continua aparecendo, mas no PS 5.1 com saída redirecionada viraria exceção fatal e silenciosa.
    Write-Step $Description
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    & $Block 2>&1 | ForEach-Object { Write-Host $(if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }) }
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($code -ne 0) { Stop-WithError "$Description falhou (código $code)" }
    Write-Ok $Description
}

$script:DevPidFile = Join-Path $RepoRoot 'var\dev-pids.json'

function Save-DevProcesses([object[]]$Processes) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $DevPidFile) | Out-Null
    $data = @($Processes | ForEach-Object { @{ pid = $_.Id; start = $_.StartTime.ToUniversalTime().Ticks; name = $_.ProcessName } })
    [IO.File]::WriteAllText($DevPidFile, (ConvertTo-Json -InputObject $data -Compress))
}

function Stop-DevProcesses {
    # Encerra (com a árvore de filhos) os processos registrados por uma execução anterior do dev.ps1.
    # Confere a hora de início para nunca matar outro programa que tenha reaproveitado o PID.
    if (-not (Test-Path $DevPidFile)) { return 0 }
    # taskkill escreve no stderr quando um filho já saiu; no PS 5.1 isso viraria exceção com 'Stop'.
    $ErrorActionPreference = 'Continue'
    $stopped = 0
    # PS 5.1: ConvertFrom-Json devolve o array inteiro como um único item no pipeline; atribuir a uma
    # variável antes do foreach faz a enumeração correta.
    $entries = Get-Content $DevPidFile -Raw | ConvertFrom-Json
    foreach ($entry in $entries) {
        $proc = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        if ($proc -and $proc.StartTime.ToUniversalTime().Ticks -eq [int64]$entry.start) {
            & taskkill.exe /T /F /PID $entry.pid 2>&1 | Out-Null
            $stopped++
        }
    }
    # O dev.ps1 que perdeu os filhos também apaga o arquivo ao sair; ausência é o estado desejado.
    Remove-Item $DevPidFile -Force -ErrorAction Ignore
    return $stopped
}
