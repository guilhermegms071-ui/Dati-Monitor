//go:build windows

package usbprint

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

// bidiScript asks the printer driver, through the Windows Bidi API (IBidiSpl), every value it can report
// ("GetAll" under \Printer): vendor drivers talk to the printer over USB in its own protocol and may expose
// counters, supplies and serial number there. The queue name comes in DM_BIDI_PRINTER; output is one
// "schema<TAB>value" per line, UTF-8.
const bidiScript = `$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace DM {
  [ComImport, Guid("8F348BD7-4B47-4755-8A9D-0F422DF3DC89"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IBidiRequest {
    void SetSchema([MarshalAs(UnmanagedType.LPWStr)] string schema);
    void SetInputData(uint type, IntPtr data, uint size);
    void GetResult(out int hr);
    void GetOutputData(uint index, out IntPtr schema, out uint type, out IntPtr data, out uint size);
    void GetEnumCount(out uint total);
  }
  [ComImport, Guid("D580DC0E-DE39-4649-BAA8-BF0B85A03A97"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IBidiSpl {
    void BindDevice([MarshalAs(UnmanagedType.LPWStr)] string device, uint access);
    void UnbindDevice();
    void SendRecv([MarshalAs(UnmanagedType.LPWStr)] string action, IBidiRequest request);
    void MultiSendRecv([MarshalAs(UnmanagedType.LPWStr)] string action, IntPtr container);
  }
  [ComImport, Guid("2A614240-A4C5-4C33-BD87-1BC709331639")] public class BidiSpl {}
  [ComImport, Guid("B9162A23-45F9-47cc-80F5-FE0FE9B9E1A2")] public class BidiRequest {}
  public static class Bidi {
    public static string[] GetAll(string printer, string root) {
      var spl = (IBidiSpl)new BidiSpl();
      spl.BindDevice(printer, 2);
      try {
        var req = (IBidiRequest)new BidiRequest();
        req.SetSchema(root);
        spl.SendRecv("GetAll", req);
        int hr; req.GetResult(out hr);
        if (hr != 0) { throw new Exception("o driver recusou GetAll (0x" + hr.ToString("X8") + ")"); }
        uint n; req.GetEnumCount(out n);
        var lines = new string[n];
        for (uint i = 0; i < n; i++) {
          IntPtr s, d; uint t, size;
          req.GetOutputData(i, out s, out t, out d, out size);
          string schema = Marshal.PtrToStringUni(s);
          string val;
          switch (t) {
            case 1: val = Marshal.ReadInt32(d).ToString(); break;
            case 3: val = (Marshal.ReadInt32(d) != 0).ToString(); break;
            case 4: case 5: case 6: val = Marshal.PtrToStringUni(d); break;
            case 7: val = "(" + size + " bytes)"; break;
            default: val = "(tipo " + t + ")"; break;
          }
          lines[i] = schema + "\t" + val;
          Marshal.FreeCoTaskMem(s);
          if (d != IntPtr.Zero) { Marshal.FreeCoTaskMem(d); }
        }
        return lines;
      } finally { spl.UnbindDevice(); }
    }
  }
}
"@
[DM.Bidi]::GetAll($env:DM_BIDI_PRINTER, '\Printer') | ForEach-Object { $_ }`

// BidiValues returns what the printer driver reports through the Windows Bidi API ("schema<TAB>value"),
// in its own process (a vendor driver may hang).
func BidiValues(ctx context.Context, printer string) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", bidiScript)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	cmd.Env = append(os.Environ(), "DM_BIDI_PRINTER="+printer)
	out, err := cmd.Output()
	if ctx.Err() != nil {
		return nil, errors.New("o driver não respondeu no prazo")
	}
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			return nil, fmt.Errorf("%s", firstNonEmpty(lastLine(string(ee.Stderr)), err.Error()))
		}
		return nil, err
	}
	var lines []string
	for _, l := range strings.Split(strings.TrimPrefix(string(out), string(rune(0xFEFF))), "\n") {
		if l = strings.TrimRight(l, "\r"); l != "" {
			lines = append(lines, l)
		}
	}
	return lines, nil
}

func lastLine(s string) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		if l := strings.TrimSpace(lines[i]); l != "" {
			return l
		}
	}
	return ""
}
