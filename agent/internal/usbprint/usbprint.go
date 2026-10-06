// Package usbprint lists the printers connected by USB to this PC (PROMPT 11) and, when the printer
// answers PJL through the USB port, reads its page counter (@PJL INFO PAGECOUNT). Printers that do not
// answer are still reported (status only) so the portal can register manual readings for them.
//
// Windows: Win32_Printer (WMI) filtered by USB ports, the parent USB device (serial) via PnP, and PJL over
// the USBPRINT device interface. Other systems: no USB inventory (the Linux agent reads network printers).
package usbprint

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// GUIDDevInterfaceUSBPrint is GUID_DEVINTERFACE_USBPRINT.
const GUIDDevInterfaceUSBPrint = "{28d78fad-5a12-11d1-ae5b-0000f803a8c2}"

// PJLTimeout is how long we wait for a PJL answer before giving up ("sem contador disponível").
const PJLTimeout = 5 * time.Second

// ErrNoAnswer means the printer did not answer PJL (common on GDI/host-based printers).
var ErrNoAnswer = errors.New("a impressora não respondeu ao PJL pela USB")

// Printer is a USB printer found on this PC.
type Printer struct {
	Name        string `json:"name"`
	Driver      string `json:"driver"`
	Port        string `json:"port"`
	PNPDeviceID string `json:"pnp_device_id"`
	// Parent is the USB device instance (USB\VID_xxxx&PID_xxxx\<serial>) of the USBPRINT node.
	Parent  string `json:"parent"`
	Offline bool   `json:"offline"`
	// Present: the USB device on this port is plugged in now (false = queue left from a printer unplugged
	// long ago, or the printer is off).
	Present bool `json:"present"`
}

// Connected keeps one printer per USB port, only the ones plugged in now: Windows keeps a queue for every
// port a printer was ever plugged into (USB001...USB006) and may have two queues for the same printer
// (another driver, "Cópia 1"). The first queue of the port wins, preferring the one that is not a copy.
func Connected(list []Printer) []Printer {
	out := make([]Printer, 0, len(list))
	at := map[string]int{}
	for _, p := range list {
		if !p.Present {
			continue
		}
		i, seen := at[strings.ToUpper(p.Port)]
		if !seen {
			at[strings.ToUpper(p.Port)] = len(out)
			out = append(out, p)
			continue
		}
		if isCopy(out[i].Name) && !isCopy(p.Name) {
			out[i] = p
		}
	}
	return out
}

func isCopy(name string) bool {
	n := strings.ToLower(name)
	return strings.Contains(n, "(cópia") || strings.Contains(n, "(copia") || strings.Contains(n, "(copy")
}

// InterfacePath is the USBPRINT device interface path built from the parent USB instance id
// (\\?\USB#VID_03F0&PID_002A#SERIAL#{28d78fad-...}).
func (p Printer) InterfacePath() string {
	if p.Parent == "" {
		return ""
	}
	return `\\?\` + strings.ReplaceAll(p.Parent, `\`, "#") + "#" + GUIDDevInterfaceUSBPrint
}

// Serial is the stable identity sent to the server: the USB serial number when the device has one
// (Windows invents ids with '&' for devices without serial), else a hash of PC + printer.
func (p Printer) Serial(hostname string) string {
	parts := strings.Split(p.Parent, `\`)
	if len(parts) == 3 && parts[2] != "" && !strings.Contains(parts[2], "&") {
		return strings.ToUpper(parts[2])
	}
	sum := sha256.Sum256([]byte(strings.ToLower(hostname + "|" + p.PNPDeviceID + "|" + p.Name)))
	return "USB-" + strings.ToUpper(hex.EncodeToString(sum[:])[:12])
}

var brands = []struct{ needle, name string }{
	{"hewlett", "HP"}, {"hp ", "HP"}, {"brother", "Brother"}, {"canon", "Canon"}, {"epson", "Epson"},
	{"samsung", "Samsung"}, {"lexmark", "Lexmark"}, {"kyocera", "Kyocera"}, {"ricoh", "Ricoh"},
	{"xerox", "Xerox"}, {"oki", "OKI"}, {"konica", "Konica Minolta"}, {"sharp", "Sharp"},
	{"toshiba", "Toshiba"}, {"pantum", "Pantum"},
}

// Brand guesses the manufacturer from the driver or printer name.
func (p Printer) Brand() string {
	s := strings.ToLower(p.Driver + " " + p.Name + " ")
	for _, b := range brands {
		if strings.Contains(s, b.needle) {
			return b.name
		}
	}
	return ""
}

// Model is the driver name (e.g. "HP LaserJet M15w") or the printer name.
func (p Printer) Model() string {
	if p.Driver != "" {
		return p.Driver
	}
	return p.Name
}

// PJL request: Universal Exit Language + INFO commands + UEL.
func pjlRequest(cmds ...string) []byte {
	var b bytes.Buffer
	b.WriteString("\x1b%-12345X@PJL\r\n")
	for _, c := range cmds {
		b.WriteString("@PJL " + c + "\r\n")
	}
	b.WriteString("\x1b%-12345X")
	return b.Bytes()
}

var pageCountRe = regexp.MustCompile(`(?is)@PJL\s+INFO\s+PAGECOUNT\s*\r?\n\s*(?:PAGECOUNT\s*=\s*)?(\d+)`)

// ParsePageCount reads the answer to @PJL INFO PAGECOUNT ("12345" or "PAGECOUNT=12345").
func ParsePageCount(resp []byte) (int64, error) {
	m := pageCountRe.FindSubmatch(resp)
	if m == nil {
		return 0, fmt.Errorf("resposta PJL sem PAGECOUNT: %q", truncate(resp, 120))
	}
	return strconv.ParseInt(string(m[1]), 10, 64)
}

var idRe = regexp.MustCompile(`(?is)@PJL\s+INFO\s+ID\s*\r?\n\s*"?([^"\r\n]+)"?`)

// ParseID reads the answer to @PJL INFO ID (model name), "" when absent.
func ParseID(resp []byte) string {
	if m := idRe.FindSubmatch(resp); m != nil {
		return strings.TrimSpace(string(m[1]))
	}
	return ""
}

func truncate(b []byte, n int) string {
	if len(b) > n {
		return string(b[:n]) + "…"
	}
	return string(b)
}

// Query sends the PJL INFO request and reads until both answers arrived (each ends with a form feed),
// the context ends or the reader closes. rw is the open USBPRINT device (or a fake in tests).
func Query(ctx context.Context, rw io.ReadWriter) (pageCount int64, model string, err error) {
	if _, err := rw.Write(pjlRequest("INFO ID", "INFO PAGECOUNT")); err != nil {
		return 0, "", fmt.Errorf("enviar PJL: %w", err)
	}
	type result struct {
		data []byte
		err  error
	}
	done := make(chan result, 1)
	go func() {
		var buf bytes.Buffer
		chunk := make([]byte, 4096)
		for {
			n, rerr := rw.Read(chunk)
			buf.Write(chunk[:n])
			if pageCountRe.Match(buf.Bytes()) && bytes.Count(buf.Bytes(), []byte("\f")) >= 2 {
				done <- result{buf.Bytes(), nil}
				return
			}
			if rerr != nil {
				done <- result{buf.Bytes(), rerr}
				return
			}
		}
	}()
	select {
	case <-ctx.Done():
		return 0, "", ErrNoAnswer
	case r := <-done:
		count, perr := ParsePageCount(r.data)
		if perr != nil {
			if r.err != nil && !errors.Is(r.err, io.EOF) {
				return 0, "", fmt.Errorf("%w: %w", ErrNoAnswer, r.err)
			}
			return 0, "", fmt.Errorf("%w: %w", ErrNoAnswer, perr)
		}
		return count, ParseID(r.data), nil
	}
}
