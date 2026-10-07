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
	"golang.org/x/sys/windows/registry"
)

// listScript queries WMI for the printer queues on USB ports (name, driver, port, spooler offline flag), the
// USB device of each port and whether that device is plugged in now (Get-PnpDevice .Present). Together with
// presentPorts (live USB printer interfaces) it tells which queues are connected: the spooler keeps queues
// of printers unplugged long ago. PowerShell 5.1 ships with every supported Windows; output is JSON in UTF-8.
const listScript = `$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
# Porta (USB001...) -> aparelho USB, pelo registro das interfaces de impressora USB. Serve para as impressoras
# cujo driver do fabricante não publica a interface padrão (a lista do Windows na hora não as mostra).
$ports = @{}
$base = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceClasses\{28d78fad-5a12-11d1-ae5b-0000f803a8c2}'
if (Test-Path $base) {
  foreach ($k in Get-ChildItem $base) {
    $dp = Get-ItemProperty -LiteralPath (Join-Path $k.PSPath '#\Device Parameters') -ErrorAction SilentlyContinue
    if (-not $dp -or $null -eq $dp.'Port Number') { continue }
    $port = [string]$dp.'Base Name' + ('{0:D3}' -f [int]$dp.'Port Number')
    $name = $k.PSChildName -replace '^##\?#', '' -replace '#\{[^}]+\}$', ''
    $parent = $name -replace '#', '\'
    $dev = Get-PnpDevice -InstanceId $parent -ErrorAction SilentlyContinue
    $live = [bool]($dev -and $dev.Present)
    if ($live -or -not $ports.ContainsKey($port)) { $ports[$port] = @{ parent = $parent; live = $live } }
  }
}
$out = @(Get-CimInstance Win32_Printer | Where-Object { $_.PortName -like 'USB*' } | ForEach-Object {
  $parent = ''
  if ($_.PNPDeviceID) {
    $prop = Get-PnpDeviceProperty -InstanceId $_.PNPDeviceID -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue
    if ($prop) { $parent = [string]$prop.Data }
  }
  $live = $false
  $mapped = $ports[[string]$_.PortName]
  if ($mapped) {
    if (-not $parent) { $parent = $mapped.parent }
    $live = $mapped.live
  }
  [pscustomobject]@{ name = $_.Name; driver = $_.DriverName; port = $_.PortName; pnp_device_id = [string]$_.PNPDeviceID; parent = [string]$parent; offline = [bool]$_.WorkOffline; device_present = [bool]$live }
})
ConvertTo-Json -InputObject $out -Compress`

// deviceClassesKey is the registry key of a USBPRINT interface; its "#\Device Parameters" holds the port
// (Base Name "USB" + Port Number 3 → USB003).
const deviceClassesKey = `SYSTEM\CurrentControlSet\Control\DeviceClasses\` + GUIDDevInterfaceUSBPrint

// presentPorts asks Windows which USB printer interfaces are plugged in now and maps each one to its port.
func presentPorts() (map[string]string, error) {
	guid, err := windows.GUIDFromString(GUIDDevInterfaceUSBPrint)
	if err != nil {
		return nil, err
	}
	paths, err := windows.CM_Get_Device_Interface_List("", &guid, windows.CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
	if err != nil {
		return nil, fmt.Errorf("impressoras USB conectadas (CM_Get_Device_Interface_List): %w", err)
	}
	out := map[string]string{}
	for _, path := range paths {
		if path == "" {
			continue
		}
		port, err := portOf(path)
		if err != nil {
			return nil, fmt.Errorf("porta da impressora USB %s: %w", path, err)
		}
		out[port] = path
	}
	return out, nil
}

func portOf(path string) (string, error) {
	name := "##?#" + strings.TrimPrefix(path, `\\?\`)
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, deviceClassesKey+`\`+name+`\#\Device Parameters`, registry.QUERY_VALUE)
	if err != nil {
		return "", err
	}
	defer func() { _ = k.Close() }()
	base, _, err := k.GetStringValue("Base Name")
	if err != nil {
		return "", fmt.Errorf("Base Name: %w", err)
	}
	num, _, err := k.GetIntegerValue("Port Number")
	if err != nil {
		return "", fmt.Errorf("Port Number: %w", err)
	}
	return strings.ToUpper(fmt.Sprintf("%s%03d", base, num)), nil
}

// List returns the USB printers connected to this PC now (one per port).
func List(ctx context.Context) ([]Printer, error) {
	all, err := listAll(ctx)
	if err != nil {
		return nil, err
	}
	return Connected(all), nil
}

// listAll returns every printer queue on a USB port, each marked connected or not.
func listAll(ctx context.Context) ([]Printer, error) {
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
	present, err := presentPorts()
	if err != nil {
		return nil, err
	}
	return ApplyPresent(list, present), nil
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

// PageCount asks the page counter by PJL: straight through the USB printer interface when Windows publishes
// it, else (or when that fails) through the printer queue, if the driver says the printer understands PJL.
func PageCount(ctx context.Context, p Printer) (int64, string, error) {
	count, model, direct := directPageCount(ctx, p)
	if direct == nil {
		return count, model, nil
	}
	if !SpoolerSafe(p.Driver) {
		return 0, "", fmt.Errorf("%w (%w); pela fila de impressão não foi tentado: o driver %q não indica suporte a PJL "+
			"(evita imprimir uma folha à toa)", ErrNoAnswer, direct, p.Driver)
	}
	count, model, spool := spoolerPageCount(ctx, p)
	if spool == nil {
		return count, model, nil
	}
	return 0, "", fmt.Errorf("%w: USB direta: %w; %w", ErrNoAnswer, direct, spool)
}

// directPageCount opens the printer's USBPRINT interface and asks the page counter by PJL.
func directPageCount(ctx context.Context, p Printer) (int64, string, error) {
	path := p.InterfacePath()
	if path == "" {
		return 0, "", errors.New("sem interface USB de impressora publicada pelo driver")
	}
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, "", err
	}
	h, err := windows.CreateFile(name, windows.GENERIC_READ|windows.GENERIC_WRITE,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, nil, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		return 0, "", fmt.Errorf("abrir %s: %w", path, err)
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
