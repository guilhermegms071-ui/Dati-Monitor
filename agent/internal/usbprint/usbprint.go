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

// PJLTimeout is how long we wait for a PJL answer before giving up ("sem contador disponível"). Copiers
// coming out of energy saving take a few seconds to answer.
const PJLTimeout = 10 * time.Second

// readIdle is the pause between reads that returned nothing yet (the USB driver answers at once, empty).
const readIdle = 50 * time.Millisecond

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
	// Path is the USBPRINT device interface of the port as Windows reports it now (empty = not plugged in).
	Path string `json:"path,omitempty"`
	// DevicePresent: the USB device of the port is plugged in now (Device Manager), even when its driver does
	// not publish the standard USB printer interface (some Canon/Epson drivers); the counter then comes
	// through the spooler.
	DevicePresent bool `json:"device_present"`
}

// ApplyPresent marks which queues are connected now: the port has a live USB printer interface (port →
// interface path, e.g. "USB003" → \\?\USB#VID_132B&PID_236C#000DE90C#{...}) or the port's USB device is
// plugged in (DevicePresent: drivers that do not publish the standard interface). The spooler and the
// registry keep ports and paths of printers plugged in long ago, so neither alone says what is connected.
func ApplyPresent(list []Printer, present map[string]string) []Printer {
	out := make([]Printer, len(list))
	for i, p := range list {
		path, ok := present[strings.ToUpper(p.Port)]
		p.Present = ok || p.DevicePresent
		p.Path = path
		if ok {
			p.Parent = parentFromPath(path)
		}
		if p.Present {
			p.Offline = false // ligada na USB agora: o "offline" do spooler pode ser de antes
		}
		out[i] = p
	}
	return out
}

// parentFromPath turns an interface path (\\?\USB#VID_x&PID_y#SERIAL#{guid}) into the USB instance id
// (USB\VID_x&PID_y\SERIAL).
func parentFromPath(path string) string {
	s := strings.TrimPrefix(path, `\\?\`)
	if i := strings.LastIndex(s, "#{"); i >= 0 {
		s = s[:i]
	}
	return strings.ReplaceAll(s, "#", `\`)
}

// Connected keeps one printer per USB port, only the ones plugged in now: Windows keeps a queue for every
// port a printer was ever plugged into (USB001...USB006) and may have two queues for the same printer
// (another driver, "Cópia 1"). The first queue of the port wins, preferring the one that is not a copy.
func Connected(list []Printer) []Printer {
	out := make([]Printer, 0, len(list))
	at := map[string]int{}
	for _, p := range list {
		if !p.Present || isFax(p) {
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

// isFax: the fax function of a multifunction printer has its own queue and USB port; it is the same printer.
func isFax(p Printer) bool {
	return strings.Contains(strings.ToUpper(p.Name+" "+p.Driver), "FAX")
}

func isCopy(name string) bool {
	n := strings.ToLower(name)
	return strings.Contains(n, "(cópia") || strings.Contains(n, "(copia") || strings.Contains(n, "(copy")
}

// InterfacePath is the USBPRINT device interface path built from the parent USB instance id
// (\\?\USB#VID_03F0&PID_002A#SERIAL#{28d78fad-...}).
func (p Printer) InterfacePath() string {
	if p.Path != "" {
		return p.Path
	}
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

// exchange writes the request and reads until complete(answer) says the whole answer arrived, the reader
// fails or the context ends (ErrNoAnswer). rw is the open USB printer (or a fake in tests).
func exchange(ctx context.Context, rw io.ReadWriter, request []byte, complete func([]byte) bool) ([]byte, error) {
	if _, err := rw.Write(request); err != nil {
		return nil, fmt.Errorf("enviar o pedido: %w", err)
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
			if n == 0 && rerr == nil {
				select {
				case <-ctx.Done():
					return
				case <-time.After(readIdle):
				}
				continue
			}
			if complete(buf.Bytes()) {
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
		return nil, ErrNoAnswer
	case r := <-done:
		return r.data, r.err
	}
}

// Query sends the PJL INFO request and reads until both answers arrived (each ends with a form feed).
func Query(ctx context.Context, rw io.ReadWriter) (pageCount int64, model string, err error) {
	data, rerr := exchange(ctx, rw, pjlRequest("INFO ID", "INFO PAGECOUNT"), func(b []byte) bool {
		return pageCountRe.Match(b) && bytes.Count(b, []byte("\f")) >= 2
	})
	if errors.Is(rerr, ErrNoAnswer) {
		return 0, "", ErrNoAnswer
	}
	count, perr := ParsePageCount(data)
	if perr != nil {
		if rerr != nil && !errors.Is(rerr, io.EOF) {
			return 0, "", fmt.Errorf("%w: %w", ErrNoAnswer, rerr)
		}
		return 0, "", fmt.Errorf("%w: %w", ErrNoAnswer, perr)
	}
	return count, ParseID(data), nil
}

// psRequest asks a PostScript printer, between Ctrl-D (end of job on USB), for its page counter, product
// name and serial number; each answer comes back on the USB channel as "%%[ key: value ]%%". Nothing is
// printed (no showpage). serialnumber is optional in PostScript, so its error is caught with "stopped".
var psRequest = []byte("\x04%!PS-Adobe-3.0\n" +
	"(%%[ pagecount: ) print statusdict /pagecount get exec 20 string cvs print ( ]%%\\n) print flush\n" +
	"{ (%%[ product: ) print product print ( ]%%\\n) print flush } stopped pop\n" +
	"{ (%%[ serial: ) print serialnumber 20 string cvs print ( ]%%\\n) print flush } stopped pop\n" +
	"(%%[ end ]%%\\n) print flush\n\x04")

var (
	psCountRe   = regexp.MustCompile(`%%\[ pagecount: (\d+) \]%%`)
	psProductRe = regexp.MustCompile(`%%\[ product: ([^\]\r\n]*?) \]%%`)
	psSerialRe  = regexp.MustCompile(`%%\[ serial: ([^\]\s]+) \]%%`)
	psEndRe     = regexp.MustCompile(`%%\[ end \]%%`)
)

// PSAnswer is what a PostScript printer told about itself.
type PSAnswer struct {
	PageCount int64
	Product   string
	Serial    string
}

// ParsePS reads the answer to psRequest.
func ParsePS(resp []byte) (PSAnswer, error) {
	m := psCountRe.FindSubmatch(resp)
	if m == nil {
		return PSAnswer{}, fmt.Errorf("resposta PostScript sem pagecount: %q", truncate(resp, 160))
	}
	n, err := strconv.ParseInt(string(m[1]), 10, 64)
	if err != nil {
		return PSAnswer{}, err
	}
	a := PSAnswer{PageCount: n}
	if p := psProductRe.FindSubmatch(resp); p != nil {
		a.Product = strings.TrimSpace(string(p[1]))
	}
	if sn := psSerialRe.FindSubmatch(resp); sn != nil && string(sn[1]) != "0" {
		a.Serial = string(sn[1])
	}
	return a, nil
}

// QueryPS asks the page counter in PostScript (printers whose USB id lists PS but not PJL, e.g. Konica).
func QueryPS(ctx context.Context, rw io.ReadWriter) (PSAnswer, error) {
	data, rerr := exchange(ctx, rw, psRequest, func(b []byte) bool { return psEndRe.Match(b) })
	if errors.Is(rerr, ErrNoAnswer) {
		if a, err := ParsePS(data); err == nil {
			return a, nil
		}
		return PSAnswer{}, fmt.Errorf("%w (PostScript)", ErrNoAnswer)
	}
	a, err := ParsePS(data)
	if err != nil {
		return PSAnswer{}, fmt.Errorf("%w: %w", ErrNoAnswer, err)
	}
	return a, nil
}

// Languages says what to ask a printer: PJL and/or PostScript, from the languages in its USB id (CMD) or,
// without it, from the driver name. PostScript is only sent to printers that speak it (others would print
// the request as text).
func Languages(deviceID, driver string) (pjl, ps bool) {
	cmd := strings.ToUpper(ParseDeviceID(deviceID)["CMD"])
	if cmd != "" {
		return strings.Contains(cmd, "PJL"), strings.Contains(cmd, "POSTSCRIPT") || hasWord(cmd, "PS")
	}
	d := " " + strings.ToLower(driver) + " "
	return SpoolerSafe(driver), strings.Contains(d, "postscript") || strings.Contains(d, " ps") || strings.Contains(d, "ps-ppd") ||
		strings.Contains(d, "br-script") || strings.Contains(d, "ps3")
}

func hasWord(list, word string) bool {
	for _, w := range strings.Split(list, ",") {
		if strings.TrimSpace(w) == word {
			return true
		}
	}
	return false
}

// pjlDrivers are driver name markers of printers that understand PJL: only these get the query through the
// spooler automatically. A host-based printer (GDI, CAPT, UFR II LT...) could print the request as text.
var pjlDrivers = []string{"pcl", "postscript", " ps", "ps3", "kx", "ufr ii", "ufrii", "pjl", "br-script", "upd"}

// SpoolerSafe reports whether the driver says the printer understands PJL.
func SpoolerSafe(driver string) bool {
	d := " " + strings.ToLower(driver)
	if strings.Contains(d, "ufr ii lt") || strings.Contains(d, "ufrii lt") || strings.Contains(d, "capt") {
		return false
	}
	for _, m := range pjlDrivers {
		if strings.Contains(d, m) {
			return true
		}
	}
	return false
}

// ParseDeviceID splits an IEEE 1284 device id ("MFG:KONICA MINOLTA;MDL:bizhub C3320i;CMD:PJL,PCL,PS;")
// into its fields (keys in upper case; "COMMAND SET" is stored as CMD, "MANUFACTURER" as MFG, "MODEL" as MDL).
func ParseDeviceID(id string) map[string]string {
	alias := map[string]string{"COMMAND SET": "CMD", "MANUFACTURER": "MFG", "MODEL": "MDL", "SERIALNUMBER": "SN", "SERN": "SN"}
	out := map[string]string{}
	for _, part := range strings.Split(id, ";") {
		k, v, ok := strings.Cut(part, ":")
		if !ok {
			continue
		}
		k = strings.ToUpper(strings.TrimSpace(k))
		if a, ok := alias[k]; ok {
			k = a
		}
		out[k] = strings.TrimSpace(v)
	}
	return out
}

// DescribeDeviceID is the short text shown to the technician: model and the languages the printer speaks.
func DescribeDeviceID(id string) string {
	f := ParseDeviceID(id)
	if len(f) == 0 {
		return ""
	}
	model := strings.TrimSpace(f["MFG"] + " " + f["MDL"])
	cmd := f["CMD"]
	switch {
	case cmd == "":
		return model + "; não informa as linguagens"
	case strings.Contains(strings.ToUpper(cmd), "PJL"):
		return model + "; fala PJL (" + cmd + ")"
	default:
		return model + "; NÃO fala PJL (" + cmd + ")"
	}
}

// ModelFromID is "fabricante modelo" from the printer's USB id ("KONICA MINOLTA C368Series"), "" without id.
func ModelFromID(id string) string {
	f := ParseDeviceID(id)
	mdl := f["MDL"]
	if mdl == "" {
		return ""
	}
	if mfg := f["MFG"]; mfg != "" && !strings.HasPrefix(strings.ToUpper(mdl), strings.ToUpper(mfg)) {
		return mfg + " " + mdl
	}
	return mdl
}
