<#
.SYNOPSIS
  Encerra o ambiente de desenvolvimento iniciado pelo scripts\dev.ps1 (útil quando a janela foi fechada
  sem Ctrl+C, ou em testes automatizados) e as sobras que ainda ocupam as portas dele (8000, 8001, 5173,
  8025, 1025, 8080 e as UDP das impressoras simuladas).
#>
. "$PSScriptRoot\common.ps1"
$n = Stop-DevProcesses
if ($n -gt 0) { Write-Ok "$n processo(s) do dev.ps1 encerrado(s)" } else { Write-Ok 'Nenhum processo do dev.ps1 registrado em execução' }

# Sobras que o registro não pega (VS Code fechado, filhos órfãos): quem ainda ocupa as portas do dev.ps1.
# Só encerra python/node/snmpsim; outro programa na porta é só avisado. O "uvicorn --reload" do Windows
# deixa a porta num filho do multiprocessing, que aparece no netstat com o PID do pai já encerrado.
$ErrorActionPreference = 'Continue'
$devNames = @('python', 'pythonw', 'node', 'snmpsim-command-responder')
$tcpPorts = @(8000, 8001, 5173, 8025, 1025, 8080)
$udpPorts = @(1161..1168) + @(11161..11168)
$owners = @()
$owners += @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $tcpPorts -contains $_.LocalPort } |
    ForEach-Object { [pscustomobject]@{ Port = "TCP $($_.LocalPort)"; Pid = [int]$_.OwningProcess } })
$owners += @(Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $udpPorts -contains $_.LocalPort } |
    ForEach-Object { [pscustomobject]@{ Port = "UDP $($_.LocalPort)"; Pid = [int]$_.OwningProcess } })
$killed = 0
foreach ($o in ($owners | Sort-Object Pid -Unique)) {
    $targets = @()
    $proc = Get-Process -Id $o.Pid -ErrorAction SilentlyContinue
    if ($proc) {
        if ($devNames -notcontains $proc.ProcessName) {
            Write-Host "AVISO $($o.Port) ocupada por $($proc.ProcessName) (PID $($o.Pid)), que não é do dev.ps1: não encerrado." -ForegroundColor Yellow
            continue
        }
        $targets += $o.Pid
    }
    # Filhos órfãos do multiprocessing que herdaram a porta do processo encerrado.
    $targets += @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*parent_pid=$($o.Pid)*" } | ForEach-Object { [int]$_.ProcessId })
    foreach ($t in $targets) {
        & taskkill.exe /T /F /PID $t 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { Write-Ok "$($o.Port): processo $t encerrado"; $killed++ }
        else { Write-Fail "$($o.Port): não consegui encerrar o processo $t (taskkill código $LASTEXITCODE)" }
    }
}
if ($killed -eq 0) { Write-Ok 'Nenhuma sobra nas portas do dev.ps1' }
