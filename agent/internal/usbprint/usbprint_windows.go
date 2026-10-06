//go:build windows

package usbprint

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
)

// listScript queries WMI for USB printers and, for each USB port (USB001...), which USB device is on it and
// whether it is connected now. Win32_Printer.PNPDeviceID is often empty: then the port's entry under
// DeviceClasses\{GUID_DEVINTERFACE_USBPRINT} gives the device (key name = interface path; "#\Device
// Parameters" = Base Name + Port Number; "#\Control\Linked" = 1 when plugged in). Queues of printers that
// were unplugged long ago point to ports with Linked = 0. PowerShell 5.1 ships with every supported
// Windows; output is JSON in UTF-8.
const listScript = `$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ports = @{}
$base = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceClasses\{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
if (Test-Path $base) {
  foreach ($k in Get-ChildItem $base) {
    $dp = Get-ItemProperty -LiteralPath (Join-Path $k.PSPath '#\Device Parameters') -ErrorAction SilentlyContinue
    if (-not $dp -or $null -eq $dp.'Port Number') { continue }
    $port = [string]$dp.'Base Name' + ('{0:D3}' -f [int]$dp.'Port Number')
    $ctl = Get-ItemProperty -LiteralPath (Join-Path $k.PSPath '#\Control') -ErrorAction SilentlyContinue
    $linked = [bool]($ctl -and $ctl.Linked -eq 1)
    $name = $k.PSChildName -replace '^##\?#', '' -replace '#\{[^}]+\}$', ''
    $parent = $name -replace '#', '\'
    if ($linked -or -not $ports.ContainsKey($port)) { $ports[$port] = @{ parent = $parent; present = $linked } }
  }
}
$out = @(Get-CimInstance Win32_Printer | Where-Object { $_.PortName -like 'USB*' } | ForEach-Object {
  $parent = ''
  if ($_.PNPDeviceID) {
    $prop = Get-PnpDeviceProperty -InstanceId $_.PNPDeviceID -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue
    if ($prop) { $parent = [string]$prop.Data }
  }
  $present = -not [bool]$_.WorkOffline
  $mapped = $ports[[string]$_.PortName]
  if ($mapped) {
    if (-not $parent) { $parent = $mapped.parent }
    $present = $present -and $mapped.present
  }
  [pscustomobject]@{ name = $_.Name; driver = $_.DriverName; port = $_.PortName; pnp_device_id = [string]$_.PNPDeviceID; parent = [string]$parent; offline = [bool]$_.WorkOffline; present = [bool]$present }
})
ConvertTo-Json -InputObject $out -Compress`

// List returns the USB printers of this PC.
func List(ctx context.Context) ([]Printer, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", listScript)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	out, err := cmd.Output()
	if err != nil {
		var stderr string
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			stderr = strings.TrimSpace(string(ee.Stderr))
		}
		return nil, fmt.Errorf("consulta WMI de impressoras USB: %w %s", err, stderr)
	}
	list, err := parseList(out)
	if err != nil {
		return nil, err
	}
	return Connected(list), nil
}

func parseList(out []byte) ([]Printer, error) {
	raw := strings.TrimSpace(strings.TrimPrefix(string(out), string(rune(0xFEFF))))
	if raw == "" || raw == "null" {
		return nil, nil
	}
	var list []Printer
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		return nil, fmt.Errorf("resposta da consulta WMI inválida: %w", err)
	}
	return list, nil
}

// handleRW adapts the USBPRINT handle to io.ReadWriter.
type handleRW struct{ h windows.Handle }

func (r handleRW) Read(p []byte) (int, error) {
	var n uint32
	err := windows.ReadFile(r.h, p, &n, nil)
	return int(n), err
}

func (r handleRW) Write(p []byte) (int, error) {
	var n uint32
	err := windows.WriteFile(r.h, p, &n, nil)
	return int(n), err
}

// PageCount opens the printer's USBPRINT interface and asks the page counter by PJL.
func PageCount(ctx context.Context, p Printer) (int64, string, error) {
	path := p.InterfacePath()
	if path == "" {
		return 0, "", fmt.Errorf("%w: dispositivo USB do spooler não identificado", ErrNoAnswer)
	}
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, "", err
	}
	h, err := windows.CreateFile(name, windows.GENERIC_READ|windows.GENERIC_WRITE,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		return 0, "", fmt.Errorf("%w: abrir %s: %w", ErrNoAnswer, path, err)
	}
	ctx, cancel := context.WithTimeout(ctx, PJLTimeout)
	stopped := make(chan struct{})
	go func() {
		<-ctx.Done()
		_ = windows.CancelIoEx(h, nil) // desbloqueia o ReadFile quando a impressora não responde
		close(stopped)
	}()
	defer func() {
		cancel()
		<-stopped // só fecha o handle depois do CancelIoEx
		_ = windows.CloseHandle(h)
	}()
	return Query(ctx, handleRW{h})
}
