<#
.SYNOPSIS
  Gera o instalador Windows do coletor (Inno Setup): compila dm-agent/dm-watchdog/dm-tool para
  windows/amd64, windows/arm64 e windows/386 e empacota em dist\installers\<slug>-setup-<versão>.exe.
.DESCRIPTION
  Nomes (produto, serviços, pasta de dados) vêm do product.json. Assinatura de código: com -Sign, assina
  os binários e o setup com o signtool do Windows SDK usando o certificado de DM_SIGN_PFX (+
  DM_SIGN_PFX_PASSWORD) ou DM_SIGN_THUMBPRINT (repositório do Windows). Sem -Sign o instalador sai sem
  assinatura (aviso no fim) — o certificado será comprado depois (PROMPT Fase 8).
.EXAMPLE
  scripts\build-installer.ps1 -Version 1.0.0 -Server https://monitor.daticopy.com.br
  scripts\build-installer.ps1 -Version 1.0.0 -Server https://monitor.daticopy.com.br -Sign
  scripts\build-installer.ps1 -TestMode        # instalador de teste (sem administrador; só verificações)
#>
param(
    [string]$Version = '0.0.0-dev',
    [string]$Server = '',
    [switch]$Sign,
    [switch]$TestMode,
    [switch]$SkipBuild,
    [string]$OutDir
)
. "$PSScriptRoot\common.ps1"

if ($Version -notmatch '^(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?$') { Stop-WithError "Versão inválida (use semver): $Version" }
$numeric = "{0}.{1}.{2}.0" -f $Matches[1], $Matches[2], $Matches[3]
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'dist\installers' }
$binDir = Join-Path $RepoRoot 'dist'
$product = Get-Content (Join-Path $RepoRoot 'product.json') -Raw -Encoding UTF8 | ConvertFrom-Json

function Find-Iscc {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { return $c } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    Stop-WithError 'Inno Setup 6 não encontrado. Instale com: winget install -e --id JRSoftware.InnoSetup --scope user'
}

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path $kits) {
        $found = Get-ChildItem $kits -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    Stop-WithError 'signtool.exe não encontrado (Windows SDK). Instale o "Windows SDK Signing Tools" ou rode sem -Sign.'
}

function Invoke-Sign([string[]]$Files) {
    $signtool = Find-SignTool
    $common = @('sign', '/fd', 'SHA256', '/tr', 'http://timestamp.digicert.com', '/td', 'SHA256', '/d', "$($product.name) — Coletor")
    if ($env:DM_SIGN_PFX) {
        if (-not (Test-Path $env:DM_SIGN_PFX)) { Stop-WithError "Certificado não encontrado: DM_SIGN_PFX=$($env:DM_SIGN_PFX)" }
        $cert = @('/f', $env:DM_SIGN_PFX)
        if ($env:DM_SIGN_PFX_PASSWORD) { $cert += @('/p', $env:DM_SIGN_PFX_PASSWORD) }
    } elseif ($env:DM_SIGN_THUMBPRINT) {
        $cert = @('/sha1', $env:DM_SIGN_THUMBPRINT)
    } else {
        Stop-WithError '-Sign precisa de DM_SIGN_PFX (arquivo .pfx, com DM_SIGN_PFX_PASSWORD) ou DM_SIGN_THUMBPRINT.'
    }
    foreach ($f in $Files) {
        & $signtool @common @cert $f
        if ($LASTEXITCODE -ne 0) { Stop-WithError "signtool falhou em $f" }
        & $signtool verify /pa /q $f
        if ($LASTEXITCODE -ne 0) { Stop-WithError "assinatura não confere em $f" }
        Write-Ok "assinado: $f"
    }
}

$iscc = Find-Iscc
if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build-agent.ps1') -Version $Version -Targets 'windows/amd64', 'windows/arm64', 'windows/386' -OutDir $binDir
    if (-not $?) { Stop-WithError 'build dos binários falhou' }
}
foreach ($arch in 'amd64', 'arm64', '386') {
    foreach ($bin in 'dm-agent', 'dm-watchdog', 'dm-tool') {
        $f = Join-Path $binDir "windows-$arch\$bin.exe"
        if (-not (Test-Path $f)) { Stop-WithError "binário ausente: $f (rode sem -SkipBuild)" }
    }
}
if ($Sign) {
    Invoke-Sign (Get-ChildItem $binDir -Recurse -Include 'dm-*.exe' | Where-Object { $_.DirectoryName -match 'windows-' } | ForEach-Object FullName)
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$defines = @(
    "/DAppVersion=$Version", "/DAppVersionNumeric=$numeric", "/DAppName=$($product.name)",
    "/DSlug=$($product.slug)", "/DServicePrefix=$($product.service_prefix)", "/DDefaultServer=$Server",
    "/DBinDir=$binDir", "/DOutDir=$OutDir"
)
if ($TestMode) { $defines += '/DTestMode=1' }
$script = Join-Path $RepoRoot 'installer\windows\dati-monitor.iss'
Write-Step "Inno Setup: $script"
$ErrorActionPreference = 'Continue'
$isccOut = & $iscc /Q @defines $script 2>&1
$isccExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($isccExit -ne 0) {
    $isccOut | ForEach-Object { Write-Host $_ }
    Stop-WithError "ISCC falhou (código $isccExit)"
}
$suffix = if ($TestMode) { '-teste' } else { '' }
$setup = Join-Path $OutDir ("{0}-setup-{1}{2}.exe" -f $product.slug, $Version, $suffix)
if (-not (Test-Path $setup)) { Stop-WithError "instalador não foi gerado: $setup" }
if ($Sign) { Invoke-Sign @($setup) }
$hash = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
Write-Ok "instalador: $setup"
Write-Ok "sha256: $hash"
if (-not $Sign -and -not $TestMode) {
    Write-Host 'AVISO: instalador sem assinatura de código (o Windows SmartScreen vai alertar). Use -Sign quando houver certificado.' -ForegroundColor Yellow
}
