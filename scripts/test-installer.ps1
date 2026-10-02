<#
.SYNOPSIS
  Testa o instalador Windows do coletor (Fase 8; critérios 15 e 16).
.DESCRIPTION
  Sem administrador (padrão): compila o instalador de TESTE e confere a recusa de Windows 7/8.1/Server
  2012 com a mensagem em português, o caminho de sistema suportado e a conferência de um código de
  cadastro no servidor (válido e inválido) sem gastá-lo.

  Com -Full (terminal COMO ADMINISTRADOR): compila o instalador real, instala em modo silencioso com um
  código de verdade, confere os 2 serviços rodando com início automático (atraso) e recuperação do SCM,
  o coletor online no servidor, reinstala por cima (atualização mantém o cadastro) e desinstala,
  conferindo que serviços, programa e pasta de dados sumiram.

  O código de cadastro vem de -Code (gerado no portal: Coletores → Novo coletor) ou é criado pela API com
  o usuário de -Email/-Password (o scripts\e2e_seed.py faz isso no desenvolvimento).
.EXAMPLE
  scripts\test-installer.ps1 -Server http://127.0.0.1:8000 -Code ABCD1234
  scripts\test-installer.ps1 -Server http://127.0.0.1:8000 -Full -Code ABCD1234     # como administrador
#>
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Code = '',
    [switch]$Full,
    [string]$Version = '0.8.0'
)
. "$PSScriptRoot\common.ps1"

$product = Get-Content (Join-Path $RepoRoot 'product.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$installers = Join-Path $RepoRoot 'dist\installers'
$logDir = Join-Path $RepoRoot 'var\installer-tests'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$failures = 0

function Assert-True([bool]$Condition, [string]$What) {
    if ($Condition) { Write-Ok $What } else { Write-Fail $What; $script:failures++ }
}

function Invoke-Setup([string]$Exe, [string[]]$SetupArgs, [string]$Name) {
    $log = Join-Path $logDir "$Name.log"
    Remove-Item $log -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $Exe -ArgumentList (@('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$log`"") + $SetupArgs) -Wait -PassThru
    $text = if (Test-Path $log) { Get-Content $log -Raw -Encoding UTF8 } else { '' }
    return @{ Exit = $p.ExitCode; Log = $text }
}

# ------------------------------------------------------------------ parte 1: sem administrador
Write-Step 'Instalador de teste (sem administrador)'
& (Join-Path $PSScriptRoot 'build-installer.ps1') -Version $Version -Server $Server -TestMode
if (-not $?) { Stop-WithError 'build do instalador de teste falhou' }
$testExe = Join-Path $installers ("{0}-setup-{1}-teste.exe" -f $product.slug, $Version)

foreach ($case in @(
        @{ Sim = '6.1'; Name = 'Windows 7' },
        @{ Sim = '6.3'; Name = 'Windows 8.1' },
        @{ Sim = '6.2-server'; Name = 'Windows Server 2012' })) {
    $r = Invoke-Setup $testExe @("/SIMULATEOS=$($case.Sim)") "recusa-$($case.Sim)"
    $expected = "RECUSADO: Este computador usa $($case.Name). Instale o coletor em um PC com Windows 10 ou mais novo na mesma rede."
    Assert-True ($r.Exit -ne 0 -and $r.Log.Contains($expected)) "recusa $($case.Name) com a mensagem do PROMPT"
}
$r = Invoke-Setup $testExe @() 'suportado'
Assert-True ($r.Log -match 'Sistema suportado: 10\.0' -and $r.Log -notmatch 'RECUSADO') 'Windows 10/11 aceito'

$r = Invoke-Setup $testExe @("/SERVER=$Server", '/CODE=ZZZZ9999') 'codigo-invalido'
Assert-True ($r.Log -match 'CODIGO RECUSADO: .*inválido') 'código inválido recusado pelo servidor (mensagem em português)'
if ($Code) {
    $r = Invoke-Setup $testExe @("/SERVER=$Server", "/CODE=$Code") 'codigo-valido'
    Assert-True ($r.Log -match 'CODIGO OK: Código válido: coletor') 'código válido conferido (sem ser usado)'
    $r = Invoke-Setup $testExe @("/SERVER=$Server", "/CODE=$Code") 'codigo-valido-2'
    Assert-True ($r.Log -match 'CODIGO OK') 'o mesmo código continua válido depois da conferência'
} else {
    Write-Host 'AVISO: sem -Code; a conferência de código válido não foi testada.' -ForegroundColor Yellow
}

# ------------------------------------------------------------------ parte 2: instalação real
if ($Full) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) { Stop-WithError '-Full precisa de um terminal COMO ADMINISTRADOR (instala serviços).' }
    if (-not $Code) { Stop-WithError '-Full precisa de -Code (gere no portal: Coletores → Novo coletor).' }
    $agentSvc = "$($product.service_prefix)Agent"
    $watchdogSvc = "$($product.service_prefix)Watchdog"
    $dataDir = Join-Path $env:ProgramData $product.service_prefix
    $appDir = Join-Path $env:ProgramFiles $product.service_prefix

    Write-Step 'Instalador real (administrador)'
    & (Join-Path $PSScriptRoot 'build-installer.ps1') -Version $Version -Server $Server -SkipBuild
    if (-not $?) { Stop-WithError 'build do instalador falhou' }
    $exe = Join-Path $installers ("{0}-setup-{1}.exe" -f $product.slug, $Version)

    $r = Invoke-Setup $exe @("/SERVER=$Server", "/CODE=$Code") 'instalar'
    Assert-True ($r.Exit -eq 0) "instalação silenciosa terminou com código 0 (foi $($r.Exit))"
    foreach ($svc in $agentSvc, $watchdogSvc) {
        $s = Get-Service $svc -ErrorAction SilentlyContinue
        Assert-True ($null -ne $s -and $s.Status -eq 'Running') "serviço $svc rodando"
        $qc = (& sc.exe qc $svc) -join "`n"
        Assert-True ($qc -match 'AUTO_START\s+\(DELAYED\)') "serviço $svc com início automático (atraso)"
        $fail = (& sc.exe qfailure $svc) -join "`n"
        Assert-True ($fail -match 'RESET_PERIOD \(in seconds\)\s*:\s*86400' -and $fail -match 'RESTART -- Delay = 5000') "recuperação do SCM em $svc"
    }
    Assert-True (Test-Path (Join-Path $dataDir 'config.json')) 'cadastro gravado em ProgramData'
    $online = $false
    for ($i = 0; $i -lt 30 -and -not $online; $i++) {
        $status = (& (Join-Path $appDir 'dm-agent.exe') status) -join "`n"
        $online = $status -match '"status":\s*"ok"'
        if (-not $online) { Start-Sleep -Seconds 2 }
    }
    Assert-True $online 'coletor saudável (/health) depois da instalação'

    $agentId = (Get-Content (Join-Path $dataDir 'config.json') -Raw | ConvertFrom-Json).agent_id
    $r = Invoke-Setup $exe @() 'atualizar'
    Assert-True ($r.Exit -eq 0) 'reinstalação por cima (atualização) sem pedir código'
    $after = (Get-Content (Join-Path $dataDir 'config.json') -Raw | ConvertFrom-Json).agent_id
    Assert-True ($after -eq $agentId) 'atualização mantém o cadastro'
    Assert-True ((Get-Service $agentSvc).Status -eq 'Running') 'coletor rodando depois da atualização'

    $uninst = Get-ChildItem $appDir -Filter 'unins*.exe' | Select-Object -First 1
    $p = Start-Process -FilePath $uninst.FullName -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
    Start-Sleep -Seconds 3
    Assert-True ($p.ExitCode -eq 0) 'desinstalador terminou com código 0'
    Assert-True ($null -eq (Get-Service $agentSvc -ErrorAction SilentlyContinue)) "serviço $agentSvc removido"
    Assert-True ($null -eq (Get-Service $watchdogSvc -ErrorAction SilentlyContinue)) "serviço $watchdogSvc removido"
    Assert-True (-not (Test-Path $dataDir)) 'pasta de dados removida'
    Assert-True (-not (Test-Path (Join-Path $appDir 'dm-agent.exe'))) 'programa removido'
}

if ($failures) { Stop-WithError "$failures verificação(ões) do instalador falharam (logs em $logDir)" }
Write-Ok "Instalador: todas as verificações passaram (logs em $logDir)"
