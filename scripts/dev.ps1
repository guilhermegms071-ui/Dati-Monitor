<#
.SYNOPSIS
  Sobe o ambiente de desenvolvimento nativo (sem Docker): API (8000), gateway (8001), worker,
  portal Vite (5173), smtp_catcher (1025/8025) e uma impressora simulada snmpsim por pasta em
  profiles\recordings\sim (porta 1160 + NN). Mostra a saída de todos com prefixo e grava em var\log.
  Ctrl+C encerra tudo. Se algum processo morrer, o erro aparece na tela e o script encerra os demais.
.PARAMETER Lan
  API (8000), gateway (8001) e portal (5173) escutam na rede (0.0.0.0): coletores e navegadores de
  outros PCs da rede local.
  Prepare antes com scripts\lan-setup.ps1 (endereço no .env e regra do Firewall só para rede Privada).
#>
param([switch]$NoReload, [switch]$Lan)
. "$PSScriptRoot\common.ps1"
Assert-Venv
$envVals = Import-DotEnv
Get-RequiredEnv $envVals 'DATABASE_URL' | Out-Null

$venvScripts = Split-Path -Parent $VenvPython
$logDir = Join-Path $RepoRoot 'var\log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not (Test-Path (Join-Path $RepoRoot 'frontend\node_modules'))) { Stop-WithError 'frontend\node_modules ausente. Rode: cd frontend; npm ci' }

$pgService = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pgService) { Stop-WithError 'Serviço do PostgreSQL não encontrado. Veja a seção 0.1 do PROMPT.md.' }
if ($pgService.Status -ne 'Running') { Stop-WithError "Serviço $($pgService.Name) está $($pgService.Status). Inicie-o (services.msc) e rode de novo." }

function Assert-PortFree([int]$Port, [string]$Proto = 'TCP') {
    $busy = if ($Proto -eq 'TCP') { Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue }
            else { Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue }
    if ($busy) {
        $procId = @($busy)[0].OwningProcess
        $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
        Stop-WithError "Porta $Proto $Port já está em uso por $name (PID $procId). Rode scripts\stop-dev.ps1 (encerra sobras de execuções anteriores) e tente de novo."
    }
}

# Ao recarregar (git pull, edição), o uvicorn espera as conexões abertas fecharem; o canal ao vivo do portal
# (SSE /api/v1/events) e o WebSocket dos coletores nunca fecham sozinhos, e a API ficava parada em
# "Waiting for connections to close" (portal com HTTP 502). Com o limite, ela fecha e volta em segundos.
$reload = if ($NoReload) { @() } else { @('--reload', '--timeout-graceful-shutdown', '3') }
$bindHost = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
if ($Lan) {
    Write-Host "AVISO API, gateway e portal escutando na rede: coletores usam $($envVals['PUBLIC_SERVER_URL']); portal em $($envVals['PUBLIC_BASE_URL']) (veja scripts\lan-setup.ps1)." -ForegroundColor Yellow
}
$services = [System.Collections.Generic.List[hashtable]]::new()
$services.Add(@{ Name = 'api'; Color = 'Cyan'; Port = 8000; Cwd = 'backend'; File = $VenvPython
    Args = @('-m', 'uvicorn', 'app.api.main:create_app', '--factory', '--host', $bindHost, '--port', '8000') + $reload })
$services.Add(@{ Name = 'gateway'; Color = 'Blue'; Port = 8001; Cwd = 'backend'; File = $VenvPython
    Args = @('-m', 'uvicorn', 'app.gateway.main:create_app', '--factory', '--host', $bindHost, '--port', '8001', '--ws', 'websockets-sansio') + $reload })
$services.Add(@{ Name = 'worker'; Color = 'Magenta'; Cwd = 'backend'; File = $VenvPython; Args = @('-m', 'app.worker.main') })
$services.Add(@{ Name = 'portal'; Color = 'Green'; Port = 5173; Cwd = 'frontend'; File = 'npm.cmd'; Args = @('run', 'dev') + $(if ($Lan) { @('--', '--host', '0.0.0.0') } else { @() }) })
$services.Add(@{ Name = 'smtp'; Color = 'Yellow'; Port = 8025; ExtraPort = 1025; Cwd = '.'; File = $VenvPython; Args = @('scripts\smtp_catcher.py') })
# Página web de impressora simulada (acesso remoto pelo túnel, seção 4.9): http://127.0.0.1:8080/
$services.Add(@{ Name = 'webprinter'; Color = 'DarkYellow'; Port = 8080; Cwd = '.'; File = $VenvPython; Args = @('scripts\printer_web_sim.py', '--port', '8080') })

$simRoot = Join-Path $RepoRoot 'profiles\recordings\sim'
$sims = Get-ChildItem $simRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(\d{2})-' -and (Test-Path (Join-Path $_.FullName 'public.snmprec')) }
if (-not $sims) { Write-Host 'AVISO nenhuma impressora simulada em profiles\recordings\sim; snmpsim não será iniciado.' -ForegroundColor Yellow }
foreach ($sim in $sims) {
    $n = [int]($sim.Name.Substring(0, 2)); $port = 1160 + $n
    $sleepyCfg = Join-Path $sim.FullName 'sleepy.json'
    $simPort = if (Test-Path $sleepyCfg) { 11100 + $n } else { $port }
    $services.Add(@{ Name = "sim$($sim.Name.Substring(0, 2))"; Color = 'DarkGray'; UdpPort = $simPort; Cwd = '.'
        File = Join-Path $venvScripts 'snmpsim-command-responder.exe'
        Args = @("--data-dir=$($sim.FullName)", "--cache-dir=$(Join-Path $RepoRoot "var\snmpsim-cache\$($sim.Name)")", "--agent-udpv4-endpoint=127.0.0.1:$simPort") })
    if (Test-Path $sleepyCfg) {
        # Impressora em economia de energia: o proxy engole a 1ª tentativa depois de ficar ociosa.
        $cfg = Get-Content $sleepyCfg -Raw | ConvertFrom-Json
        $services.Add(@{ Name = "sono$($sim.Name.Substring(0, 2))"; Color = 'DarkGray'; UdpPort = $port; Cwd = '.'; File = $VenvPython
            Args = @('scripts\sleepy_udp_proxy.py', "--listen=127.0.0.1:$port", "--target=127.0.0.1:$simPort",
                "--idle=$($cfg.idle_seconds)", "--wake=$($cfg.wake_seconds)") })
    }
}

$leftover = Stop-DevProcesses
if ($leftover -gt 0) {
    Write-Host "AVISO $leftover processo(s) de uma execução anterior do dev.ps1 ainda estavam rodando e foram encerrados." -ForegroundColor Yellow
    Start-Sleep -Seconds 1
}

foreach ($s in $services) {
    if ($s.ContainsKey('Port')) { Assert-PortFree $s.Port }
    if ($s.ContainsKey('ExtraPort')) { Assert-PortFree $s.ExtraPort }
    if ($s.ContainsKey('UdpPort')) { Assert-PortFree $s.UdpPort 'UDP' }
}

Push-Location (Join-Path $RepoRoot 'backend')
try {
    Invoke-Checked 'Banco: migrações (alembic upgrade head)' { & $VenvPython -m app.cli migrate }
    if ($envVals['DEV_SEED'] -eq 'false') {
        Write-Ok 'Banco: sem dados de exemplo (DEV_SEED=false no .env; veja scripts\reset-dev-db.ps1)'
    } else {
        Invoke-Checked 'Banco: dados de desenvolvimento (seed idempotente)' { & $VenvPython -m app.cli seed-dev }
    }
} finally { Pop-Location }

$running = @()
function Stop-All {
    $ErrorActionPreference = 'Continue'  # taskkill pode reclamar de filhos que já saíram
    foreach ($r in $running) {
        if (-not $r.Process.HasExited) { & taskkill.exe /T /F /PID $r.Process.Id 2>&1 | Out-Null }
    }
}

try {
    foreach ($s in $services) {
        $out = Join-Path $logDir "$($s.Name).log"
        $err = Join-Path $logDir "$($s.Name).err.log"
        [System.IO.File]::WriteAllText($out, ''); [System.IO.File]::WriteAllText($err, '')
        $quoted = $s.Args | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
        # Cada serviço no próprio console (oculto): o "uvicorn --reload" do Windows reinicia o processo
        # filho com CTRL_C_EVENT, que atinge TODOS os processos do mesmo console — com console
        # compartilhado, salvar um arquivo derrubava o dev.ps1, o worker e os simuladores.
        $p = Start-Process -FilePath $s.File -ArgumentList $quoted -WorkingDirectory (Join-Path $RepoRoot $s.Cwd) `
            -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
        $null = $p.Handle  # garante ExitCode disponível após o término
        $running += @{ Name = $s.Name; Color = $s.Color; Process = $p; Files = @($out, $err); Pos = @{ $out = 0L; $err = 0L } }
        Write-Host ("[{0,-8}] iniciado (PID {1})" -f $s.Name, $p.Id) -ForegroundColor $s.Color
    }

    Save-DevProcesses ($running | ForEach-Object { $_.Process })

    Write-Step 'Aguardando a API responder em http://127.0.0.1:8000/api/health'
    $deadline = (Get-Date).AddSeconds(60); $ready = $false
    while ((Get-Date) -lt $deadline -and -not $ready) {
        try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; $ready = $h.status -eq 'ok' } catch { Start-Sleep -Milliseconds 500 }
    }
    if ($ready) {
        Write-Ok 'Ambiente no ar:'
        Write-Host '    Portal ........ http://localhost:5173'
        Write-Host '    API ........... http://127.0.0.1:8000/api/health   (docs: /docs)'
        Write-Host '    Gateway ....... http://127.0.0.1:8001/health'
        Write-Host '    E-mails ....... http://127.0.0.1:8025   (SMTP em 127.0.0.1:1025)'
        foreach ($sim in $sims) { Write-Host ("    Impressora .... udp 127.0.0.1:{0}  ({1})" -f (1160 + [int]$sim.Name.Substring(0, 2)), $sim.Name) }
        Write-Host '    Ctrl+C encerra tudo. Logs em var\log\'
    } else {
        Write-Fail 'A API não ficou saudável em 60 s. Veja var\log\api.err.log (os processos continuam rodando).'
    }

    while ($true) {
        foreach ($r in $running) {
            foreach ($f in $r.Files) {
                $fs = [System.IO.File]::Open($f, 'Open', 'Read', 'ReadWrite')
                try {
                    if ($fs.Length -gt $r.Pos[$f]) {
                        $fs.Seek($r.Pos[$f], 'Begin') | Out-Null
                        $reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
                        $text = $reader.ReadToEnd()
                        $r.Pos[$f] = $fs.Length
                        foreach ($line in ($text -split "`r?`n")) {
                            if ($line -ne '') { Write-Host ("[{0,-8}] {1}" -f $r.Name, $line) -ForegroundColor $r.Color }
                        }
                    }
                } finally { $fs.Dispose() }
            }
            if ($r.Process.HasExited) {
                Stop-WithError ("{0} terminou inesperadamente (código {1}). Veja var\log\{0}.err.log. Encerrando os demais." -f $r.Name, $r.Process.ExitCode)
            }
        }
        Start-Sleep -Milliseconds 300
    }
} finally {
    Write-Step 'Encerrando todos os processos'
    Stop-All
    Remove-Item $DevPidFile -Force -ErrorAction Ignore  # pode já ter sido removido pelo stop-dev.ps1
    Write-Ok 'Ambiente encerrado'
}
