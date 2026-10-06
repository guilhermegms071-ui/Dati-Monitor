<#
.SYNOPSIS
  Diagnóstico das impressoras USB deste PC (só leitura): o que o Windows informa ao coletor.
.DESCRIPTION
  Mostra as filas de impressão em portas USB (Win32_Printer) e, para cada porta USB (USB001...), o aparelho
  registrado e se está conectado agora (DeviceClasses do USBPRINT). Rode como administrador no PC da
  impressora USB e mande a saída para o suporte.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\usb-diagnostico.ps1
#>
$ErrorActionPreference = 'Continue'
Write-Host '==> Filas de impressão em portas USB (Win32_Printer)' -ForegroundColor Cyan
Get-CimInstance Win32_Printer | Where-Object { $_.PortName -like 'USB*' } |
    Select-Object Name, DriverName, PortName, WorkOffline, PrinterStatus, PNPDeviceID | Format-Table -AutoSize | Out-String -Width 250 | Write-Host

Write-Host '==> Portas USB registradas (DeviceClasses USBPRINT)' -ForegroundColor Cyan
$base = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceClasses\{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
if (-not (Test-Path $base)) {
    Write-Host "Chave não existe: $base" -ForegroundColor Yellow
} else {
    foreach ($k in Get-ChildItem $base) {
        Write-Host "--- $($k.PSChildName)"
        foreach ($sub in Get-ChildItem -LiteralPath $k.PSPath -Recurse) {
            $props = Get-ItemProperty -LiteralPath $sub.PSPath
            $vals = $props.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { "$($_.Name)=$($_.Value)" }
            Write-Host ("    {0}: {1}" -f ($sub.PSPath -replace '^.*\{28d78fad[^}]*\}\\[^\\]+', ''), ($vals -join '; '))
        }
    }
}

Write-Host '==> Aparelhos USB de impressão presentes agora (PnP)' -ForegroundColor Cyan
Get-PnpDevice -Class Printer, USB -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USBPRINT*' -or $_.FriendlyName -like '*print*' -or $_.FriendlyName -like '*Konica*' } |
    Select-Object Status, Class, FriendlyName, InstanceId | Format-Table -AutoSize | Out-String -Width 250 | Write-Host
