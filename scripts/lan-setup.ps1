<#
.SYNOPSIS
  Prepara este PC para receber coletores de outros PCs da rede local, sem hospedagem.
.DESCRIPTION
  1. Descobre o IPv4 da placa com rota padrão (ou usa -Address) e confere se é de rede privada.
     Mostra se o nome do PC resolve pelo DNS da rede. Para http:// o coletor só aceita IP (nome não):
     o que um nome resolve pode mudar, então o instalador de teste grava o IP.
  2. Grava no .env PUBLIC_SERVER_URL=http://IP:8000 e PUBLIC_WS_URL=ws://IP:8001/ws/agent.
  3. Cria a regra "Dati Monitor dev" no Firewall do Windows (TCP 8000 e 8001, entrada, só perfil Privado).
     Sem administrador, abre um PowerShell elevado só para essa parte (confirme o UAC).
  Depois: scripts\dev.ps1 -Lan
.EXAMPLE
  scripts\lan-setup.ps1
  scripts\lan-setup.ps1 -Address 10.10.10.25
  scripts\lan-setup.ps1 -Remove        # remove a regra e volta o .env para 127.0.0.1
#>
param(
    [string]$Address = '',
    [switch]$Remove,
    [switch]$FirewallOnly
)
. "$PSScriptRoot\common.ps1"

$RuleName = 'Dati Monitor dev'

function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Set-FirewallRule([bool]$Present) {
    if (-not (Test-Admin)) {
        Write-Step 'Firewall: abrindo um PowerShell como administrador (confirme o UAC)'
        $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-FirewallOnly')
        if (-not $Present) { $argList += '-Remove' }
        $p = Start-Process powershell.exe -ArgumentList $argList -Verb RunAs -Wait -PassThru
        if ($p.ExitCode -ne 0) { Stop-WithError "A regra do Firewall não foi alterada (código $($p.ExitCode))." }
        return
    }
    $existing = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
    if ($existing) { $existing | Remove-NetFirewallRule }
    if ($Present) {
        New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP `
            -LocalPort 8000, 8001 -Profile Private -Description 'API (8000) e gateway (8001) do Dati Monitor para coletores da rede local' | Out-Null
        Write-Ok "regra `"$RuleName`" criada: TCP 8000 e 8001, entrada, só rede Privada"
    } else {
        Write-Ok "regra `"$RuleName`" removida"
    }
}

function Set-EnvValues([hashtable]$Values) {
    $path = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $path)) { Stop-WithError ".env não encontrado em $path. Rode scripts\init-env.ps1." }
    $lines = [System.Collections.Generic.List[string]]::new()
    $seen = @{}
    foreach ($line in Get-Content $path -Encoding utf8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=' -and $Values.ContainsKey($Matches[1])) {
            $lines.Add("$($Matches[1])=$($Values[$Matches[1]])"); $seen[$Matches[1]] = $true
        } else { $lines.Add($line) }
    }
    foreach ($k in $Values.Keys) { if (-not $seen[$k]) { $lines.Add("$k=$($Values[$k])") } }
    [IO.File]::WriteAllLines($path, $lines, [Text.UTF8Encoding]::new($false))
    foreach ($k in $Values.Keys) { Write-Ok ".env: $k=$($Values[$k])" }
}

function Test-PrivateIPv4([string]$Ip) {
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Ip, [ref]$parsed) -or $parsed.AddressFamily -ne 'InterNetwork') { return $false }
    $b = $parsed.GetAddressBytes()
    return ($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168)
}

if ($FirewallOnly) {
    try { Set-FirewallRule (-not $Remove) } catch { Write-Fail $_.Exception.Message; Read-Host 'Enter fecha'; exit 1 }
    exit 0
}

if ($Remove) {
    Set-FirewallRule $false
    Set-EnvValues @{ PUBLIC_SERVER_URL = 'http://127.0.0.1:8000'; PUBLIC_WS_URL = 'ws://127.0.0.1:8001/ws/agent' }
    Write-Ok 'pronto: rode scripts\dev.ps1 (sem -Lan)'
    exit 0
}

# Placas com rota padrão (a de menor métrica primeiro) e o IPv4 privado de cada uma. Uma placa pode ter
# rota padrão sem IPv4 válido (VPN, só 169.254.x): é pulada.
$route = $null
$routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric)
foreach ($r in $routes) {
    $ips = @(Get-NetIPAddress -InterfaceIndex $r.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { Test-PrivateIPv4 $_.IPAddress } | ForEach-Object { $_.IPAddress })
    if ($ips.Count -eq 0) { continue }
    if (-not $Address) { $Address = $ips[0]; $route = $r; break }
    if ($ips -contains $Address) { $route = $r; break }
}
if (-not $Address) { Stop-WithError 'Não achei o IPv4 de rede privada deste PC: informe com -Address (ex.: -Address 10.10.10.25).' }
if (-not (Test-PrivateIPv4 $Address)) { Stop-WithError "$Address não é IP de rede privada (10.x, 172.16-31.x, 192.168.x)." }
Write-Ok "IP deste PC: $Address"

if ($route) {
    $netProfile = Get-NetConnectionProfile -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue
    if ($netProfile -and $netProfile.NetworkCategory -ne 'Private') {
        Write-Host "AVISO a rede `"$($netProfile.Name)`" está como $($netProfile.NetworkCategory): a regra só vale para rede Privada. Mude em Configurações > Rede." -ForegroundColor Yellow
    }
    $dns = @((Get-DnsClientServerAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses)
    $resolved = $false
    foreach ($server in $dns) {
        try {
            $r = Resolve-DnsName $env:COMPUTERNAME -Server $server -DnsOnly -Type A -ErrorAction Stop
            if ($r.IPAddress -contains $Address) { $resolved = $true }
        } catch {
            Write-Host "    DNS $server não resolve $env:COMPUTERNAME ($($_.Exception.Message))" -ForegroundColor DarkGray
        }
    }
    if ($resolved) { Write-Ok "o nome $env:COMPUTERNAME resolve para $Address no DNS da rede" }
    else { Write-Host "AVISO o nome $env:COMPUTERNAME não resolve no DNS da rede: use o IP." -ForegroundColor Yellow }
}

Set-EnvValues @{ PUBLIC_SERVER_URL = "http://${Address}:8000"; PUBLIC_WS_URL = "ws://${Address}:8001/ws/agent" }
Set-FirewallRule $true
Write-Host ''
Write-Host "IMPORTANTE reserve o IP $Address para este PC no roteador (DHCP): o instalador de teste grava esse endereço." -ForegroundColor Yellow
Write-Host 'Próximos passos:'
Write-Host '    scripts\dev.ps1 -Lan'
Write-Host "    scripts\build-installer.ps1 -Version 1.0.0 -Server http://${Address}:8000 -InsecureLan"
