<#
.SYNOPSIS
  Roda os testes: Go (com -race e cobertura >= 80% em internal/), Python (pytest, PostgreSQL real
  dati_test, cobertura >= 80%) e frontend (Vitest). Com -E2E roda também o Playwright.
.EXAMPLE
  scripts\test.ps1
  scripts\test.ps1 -E2E
#>
param([switch]$E2E)
. "$PSScriptRoot\common.ps1"
Assert-Venv
Import-DotEnv | Out-Null

$go = Get-GoExe
$varDir = Join-Path $RepoRoot 'var'
New-Item -ItemType Directory -Force -Path $varDir | Out-Null

# O detector de corridas (-race) exige CGO e um compilador C (gcc) no PATH.
$gcc = Get-Command gcc.exe -ErrorAction SilentlyContinue
if (-not $gcc) {
    $winlibs = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'BrechtSanders.WinLibs*' -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem $_.FullName -Recurse -Filter gcc.exe -ErrorAction SilentlyContinue } | Select-Object -First 1
    if ($winlibs) { $env:Path = "$(Split-Path -Parent $winlibs.FullName);$env:Path" }
    else { Stop-WithError 'gcc não encontrado (necessário para go test -race). Instale com: winget install -e --id BrechtSanders.WinLibs.POSIX.UCRT' }
}

Push-Location (Join-Path $RepoRoot 'agent')
try {
    $coverFile = Join-Path $varDir 'go-cover.out'
    $env:CGO_ENABLED = '1'
    # -tags integration inclui os testes contra o snmpsim real (usa o venv do projeto).
    Invoke-Checked 'Go: go test -race (unitários + integração com snmpsim)' {
        & $go test -race -count=1 -tags integration "-coverprofile=$coverFile" '-coverpkg=./internal/...' ./...
    }
    $total = (& $go tool cover "-func=$coverFile" | Select-String '^total:').Line
    $pct = [double](($total -split '\s+')[-1] -replace '%', '')
    if ($pct -lt 80) { Stop-WithError "Cobertura Go em internal/ abaixo de 80%: $pct%" }
    Write-Ok "Cobertura Go em internal/: $pct%"
} finally { Pop-Location; Remove-Item Env:CGO_ENABLED -ErrorAction SilentlyContinue }

Push-Location (Join-Path $RepoRoot 'backend')
try {
    Invoke-Checked 'Python: pytest (PostgreSQL dati_test) com cobertura >= 80%' {
        & $VenvPython -m pytest --cov=app --cov-report=term-missing --cov-fail-under=80
    }
} finally { Pop-Location }

Push-Location (Join-Path $RepoRoot 'frontend')
try {
    Invoke-Checked 'Frontend: vitest' { & npm.cmd run --silent test }
    if ($E2E) {
        Invoke-Checked 'Frontend: Playwright E2E' { & npx.cmd playwright test }
    }
} finally { Pop-Location }

Write-Ok 'Todos os testes passaram'
