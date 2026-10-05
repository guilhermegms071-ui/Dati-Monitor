<#
.SYNOPSIS
  Pacote de uma versão (Fase 11): binários dos 7 alvos, assinaturas ed25519 do dm-agent e do dm-watchdog
  (para publicar em Versões), instalador Windows, pacotes Linux, build do portal e SHA256SUMS, em
  dist\release-<versão>\.
.DESCRIPTION
  Exige a árvore do git limpa (o pacote corresponde a um commit). A chave PRIVADA de versões fica fora do
  repositório (%USERPROFILE%\.dati-monitor\release-signing.key) e nunca é copiada para o pacote.
  -SignCode assina também o código (signtool) quando houver certificado (DM_SIGN_PFX ou DM_SIGN_THUMBPRINT).
.EXAMPLE
  scripts\release.ps1 -Version 1.0.0
  scripts\release.ps1 -Version 1.0.1 -Server https://monitor.exemplo.com.br -SignCode
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Server = '',
    [switch]$SignCode,
    [switch]$AllowDirty
)
. "$PSScriptRoot\common.ps1"
Assert-Venv

if ($Version -notmatch '^\d+\.\d+\.\d+$') { Stop-WithError "Versão de release inválida (use x.y.z): $Version" }
$dirty = & git -C $RepoRoot status --porcelain
if ($dirty -and -not $AllowDirty) { Stop-WithError 'árvore do git com alterações: faça o commit antes (ou -AllowDirty para testar)' }
$commit = (& git -C $RepoRoot rev-parse --short HEAD).Trim()
$key = Join-Path $env:USERPROFILE '.dati-monitor\release-signing.key'
if (-not (Test-Path $key)) { Stop-WithError "chave privada de versões não encontrada em $key" }

$rel = Join-Path $RepoRoot "dist\release-$Version"
if (Test-Path $rel) { Stop-WithError "$rel já existe: apague-a para gerar de novo" }
New-Item -ItemType Directory -Force -Path (Join-Path $rel 'assinaturas'), (Join-Path $rel 'coletor') | Out-Null

Invoke-Checked "binários $Version (7 alvos)" { & (Join-Path $PSScriptRoot 'build-agent.ps1') -Version $Version }
$dist = Join-Path $RepoRoot 'dist'
$tool = Join-Path $dist 'windows-amd64\dm-tool.exe'
foreach ($dir in Get-ChildItem $dist -Directory | Where-Object { $_.Name -match '^(windows|linux)-(amd64|386|arm64|arm)$' }) {
    $goos, $goarch = $dir.Name -split '-', 2
    $target = Join-Path $rel "coletor\$($dir.Name)"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    foreach ($comp in 'agent', 'watchdog', 'tool') {
        $exe = Join-Path $dir.FullName ("dm-$comp" + $(if ($goos -eq 'windows') { '.exe' } else { '' }))
        if (-not (Test-Path $exe)) { Stop-WithError "binário ausente: $exe" }
        Copy-Item $exe $target
        if ($comp -eq 'tool') { continue }
        $json = Join-Path $rel "assinaturas\$goos-$goarch-dm-$comp.json"
        Invoke-Checked "assinatura dm-$comp $goos/$goarch" {
            $out = & $tool sign --file $exe --version $Version --os $goos --arch $goarch --key $key
            # Sem BOM: o JSON é colado na tela Versões.
            [IO.File]::WriteAllText($json, (($out -join "`n") + "`n"))
        }
    }
}

# Splat por hashtable: com array, o PowerShell passa '-Version' como valor posicional.
$installerArgs = @{ Version = $Version; SkipBuild = $true; OutDir = (Join-Path $rel 'instaladores') }
if ($Server) { $installerArgs.Server = $Server }
if ($SignCode) { $installerArgs.Sign = $true }
Invoke-Checked 'instalador Windows' { & (Join-Path $PSScriptRoot 'build-installer.ps1') @installerArgs }
Invoke-Checked 'pacotes Linux (.deb e .tar.gz)' {
    & $VenvPython (Join-Path $PSScriptRoot 'build_linux.py') --version $Version --out (Join-Path $rel 'instaladores')
}

Push-Location (Join-Path $RepoRoot 'frontend')
try { Invoke-Checked 'build do portal' { & npm.cmd run --silent build } } finally { Pop-Location }
Compress-Archive -Path (Join-Path $RepoRoot 'frontend\dist\*') -DestinationPath (Join-Path $rel "portal-$Version.zip")
Copy-Item (Join-Path $RepoRoot 'RELEASE_NOTES.md') $rel -ErrorAction SilentlyContinue

# SHA256SUMS de tudo (formato do sha256sum: "<hash>  <caminho>").
$lines = Get-ChildItem $rel -Recurse -File | Where-Object { $_.Name -ne 'SHA256SUMS' } | Sort-Object FullName | ForEach-Object {
    "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.FullName.Substring($rel.Length + 1).Replace('\', '/')
}
[IO.File]::WriteAllText((Join-Path $rel 'SHA256SUMS'), ($lines -join "`n") + "`n")
$manifest = [ordered]@{ version = $Version; commit = $commit; created_at = (Get-Date).ToUniversalTime().ToString('o'); files = $lines.Count }
[IO.File]::WriteAllText((Join-Path $rel 'release.json'), (ConvertTo-Json $manifest))
Write-Ok "pacote $Version (commit $commit) em $rel — $($lines.Count) arquivos"
