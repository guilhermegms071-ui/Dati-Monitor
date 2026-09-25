<#
.SYNOPSIS
  Roda todos os linters/formatadores em modo verificação: golangci-lint, ruff, mypy --strict,
  eslint, prettier e tsc. Falha (código 1) no primeiro problema encontrado.
#>
. "$PSScriptRoot\common.ps1"
Assert-Venv

$go = Get-GoExe
$gopath = & $go env GOPATH
$golangci = Join-Path $gopath 'bin\golangci-lint.exe'
if (-not (Test-Path $golangci)) {
    Stop-WithError "golangci-lint não encontrado em $golangci. Instale com: go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.14.0"
}
$venvScripts = Split-Path -Parent $VenvPython

Push-Location (Join-Path $RepoRoot 'agent')
try {
    Invoke-Checked 'Go: go generate sem diferenças' {
        & $go generate ./...
        if ($LASTEXITCODE -eq 0) {
            & git -C $RepoRoot diff --quiet -- agent/internal/product/product_gen.go
            if ($LASTEXITCODE -ne 0) { Write-Fail 'product_gen.go desatualizado: rode go generate e faça commit' }
        }
    }
    Invoke-Checked 'Go: golangci-lint (Windows)' { & $golangci run --build-tags integration ./... }
    # Os arquivos *_other.go / *_linux.go só compilam para Linux: o agente também roda em Linux/Raspberry.
    $env:GOOS = 'linux'
    try {
        Invoke-Checked 'Go: golangci-lint (GOOS=linux)' { & $golangci run --build-tags integration ./... }
    } finally { Remove-Item Env:GOOS -ErrorAction SilentlyContinue }
} finally { Pop-Location }

Push-Location (Join-Path $RepoRoot 'backend')
try {
    $scriptsPy = @('..\scripts\smtp_catcher.py', '..\scripts\sleepy_udp_proxy.py', '..\scripts\gen_protocol_docs.py',
        '..\profiles\recordings\sim\generate.py')
    $pyTargets = @('app', 'tests', 'alembic') + $scriptsPy
    Invoke-Checked 'Python: ruff check' { & "$venvScripts\ruff.exe" check @pyTargets }
    Invoke-Checked 'Python: ruff format --check' { & "$venvScripts\ruff.exe" format --check @pyTargets }
    Invoke-Checked 'Python: mypy --strict' { & "$venvScripts\mypy.exe" --strict app tests @scriptsPy }
} finally { Pop-Location }

Push-Location (Join-Path $RepoRoot 'frontend')
try {
    Invoke-Checked 'Frontend: eslint' { & npm.cmd run --silent lint }
    Invoke-Checked 'Frontend: prettier --check' { & npm.cmd run --silent format:check }
    Invoke-Checked 'Frontend: tsc --noEmit' { & npm.cmd run --silent typecheck }
} finally { Pop-Location }

Write-Ok 'Lint completo sem problemas'
