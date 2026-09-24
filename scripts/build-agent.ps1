<#
.SYNOPSIS
  Compila dm-agent, dm-watchdog e dm-tool para todos os alvos suportados (CGO desligado).
.EXAMPLE
  scripts\build-agent.ps1                      # todos os alvos, versão 0.0.0-dev
  scripts\build-agent.ps1 -Version 1.2.3       # versão explícita
  scripts\build-agent.ps1 -Targets windows/amd64
#>
param(
    [string]$Version = '0.0.0-dev',
    [string[]]$Targets = @('windows/amd64', 'windows/386', 'windows/arm64', 'linux/amd64', 'linux/386', 'linux/arm64', 'linux/arm'),
    [string]$OutDir
)
. "$PSScriptRoot\common.ps1"

if ($Version -notmatch '^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$') { Stop-WithError "Versão inválida (use semver): $Version" }
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'dist' }
$go = Get-GoExe
$binaries = 'dm-agent', 'dm-watchdog', 'dm-tool'

# PowerShell 5.1: stderr redirecionado de programa nativo vira erro fatal com ErrorAction Stop.
$ErrorActionPreference = 'Continue'
$commit = (& git -C $RepoRoot rev-parse --short HEAD 2>$null)
$gitExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($gitExit -ne 0 -or -not $commit) { $commit = 'sem-commit' }
$pkg = 'github.com/daticopy/dati-monitor/agent/internal/buildinfo'
$ldflags = "-s -w -X $pkg.Version=$Version -X $pkg.Commit=$commit"

$saved = @{ GOOS = $env:GOOS; GOARCH = $env:GOARCH; GOARM = $env:GOARM; CGO_ENABLED = $env:CGO_ENABLED }
Push-Location (Join-Path $RepoRoot 'agent')
try {
    $count = 0
    foreach ($target in $Targets) {
        $parts = $target -split '/'
        if ($parts.Count -ne 2) { Stop-WithError "Alvo inválido: $target (use os/arch)" }
        $env:GOOS = $parts[0]; $env:GOARCH = $parts[1]; $env:CGO_ENABLED = '0'
        $env:GOARM = if ($parts[1] -eq 'arm') { '6' } else { '' }
        $dir = Join-Path $OutDir ("{0}-{1}" -f $parts[0], $parts[1])
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        foreach ($bin in $binaries) {
            $exe = if ($parts[0] -eq 'windows') { "$bin.exe" } else { $bin }
            $out = Join-Path $dir $exe
            & $go build -trimpath -ldflags $ldflags -o $out "./cmd/$bin"
            if ($LASTEXITCODE -ne 0) { Stop-WithError "go build falhou: $bin para $target" }
            $count++
        }
        Write-Ok "$target -> $dir"
    }
    Write-Ok "$count binários gerados em $OutDir (versão $Version, commit $commit)"
} finally {
    Pop-Location
    foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
}
