// Package printer reads standard Printer-MIB / Host-Resources data (PROMPT 6.2/6.3): identity,
// normalized status with MSB-first error bits, and the supplies table.
package printer

import (
	"context"
	"sort"
	"strconv"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// Standard OIDs (PROMPT 6.2).
const (
	OIDSysDescr        = "1.3.6.1.2.1.1.1.0"
	OIDSysObjectID     = "1.3.6.1.2.1.1.2.0"
	OIDSysUpTime       = "1.3.6.1.2.1.1.3.0"
	OIDSysName         = "1.3.6.1.2.1.1.5.0"
	OIDSysLocation     = "1.3.6.1.2.1.1.6.0"
	OIDHrDeviceType    = "1.3.6.1.2.1.25.3.2.1.2.1"
	OIDHrDeviceDescr   = "1.3.6.1.2.1.25.3.2.1.3.1"
	OIDHrDeviceStatus  = "1.3.6.1.2.1.25.3.2.1.5.1"
	OIDHrPrinterStatus = "1.3.6.1.2.1.25.3.5.1.1.1"
	OIDHrPrinterError  = "1.3.6.1.2.1.25.3.5.1.2.1"
	OIDSerial          = "1.3.6.1.2.1.43.5.1.1.17.1"
	OIDLifeCount       = "1.3.6.1.2.1.43.10.2.1.4.1.1"
	OIDSupplies        = "1.3.6.1.2.1.43.11.1.1"
	OIDColorantValue   = "1.3.6.1.2.1.43.12.1.1.4"
	OIDConsoleText     = "1.3.6.1.2.1.43.16.5.1.2.1"
	OIDAlertTable      = "1.3.6.1.2.1.43.18.1.1"
	OIDIfPhysAddress   = "1.3.6.1.2.1.2.2.1.6"
	PrinterDeviceType  = "1.3.6.1.2.1.25.3.1.5"
)

// Normalized statuses (PROMPT 6.3).
const (
	StatusReady        = "ready"
	StatusPrinting     = "printing"
	StatusWarmup       = "warmup"
	StatusEnergySaving = "energy_saving"
	StatusWarning      = "warning"
	StatusError        = "error"
	StatusOffline      = "offline"
)

// ErrorFlags are the hrPrinterDetectedErrorState bits in order: bit 0 is the MOST significant bit of
// the first byte (RFC 3805 / PROMPT 6.2). ErrorBits in readings use bit i = ErrorFlags[i].
var ErrorFlags = []string{
	"lowPaper", "noPaper", "lowToner", "noToner", "doorOpen", "jammed", "offline", "serviceRequested",
	"inputTrayMissing", "outputTrayMissing", "markerSupplyMissing", "outputNearFull", "outputFull",
	"inputTrayEmpty", "overduePreventMaint",
}

var errorLevel = map[string]bool{
	"noPaper": true, "noToner": true, "doorOpen": true, "jammed": true, "offline": true,
	"serviceRequested": true, "inputTrayMissing": true, "outputTrayMissing": true,
	"markerSupplyMissing": true, "outputFull": true,
}

// DecodeErrorBits decodes the OCTET STRING of hrPrinterDetectedErrorState (MSB-first).
// It returns the normalized bitmask (bit i = ErrorFlags[i]) and the flag names, in order.
func DecodeErrorBits(b []byte) (int, []string) {
	mask := 0
	var names []string
	for i, name := range ErrorFlags {
		byteIdx, bit := i/8, 7-uint(i%8) //nolint:gosec // G115: i%8 < 8
		if byteIdx >= len(b) {
			break
		}
		if b[byteIdx]&(1<<bit) != 0 {
			mask |= 1 << i
			names = append(names, name)
		}
	}
	return mask, names
}

// Alert is one row of prtAlertTable (RFC 3805). Index is prtAlertIndex (last part of the row index)
// and Time is prtAlertTime (sysUpTime when the alert was added): together they identify each new alert.
type Alert struct {
	Index         int    `json:"index,omitempty"`
	Severity      int    `json:"severity"`
	TrainingLevel int    `json:"training_level,omitempty"`
	Group         int    `json:"group,omitempty"`
	GroupIndex    int    `json:"group_index,omitempty"`
	Location      int    `json:"location,omitempty"`
	Code          int    `json:"code"`
	Description   string `json:"description"`
	Time          int64  `json:"time,omitempty"`
}

// StatusResult is the normalized status of a device.
type StatusResult struct {
	Status        string   `json:"status"`
	ErrorBits     int      `json:"error_bits"`
	Reasons       []string `json:"reasons,omitempty"`
	PanelText     string   `json:"panel_text,omitempty"`
	DeviceStatus  int      `json:"device_status,omitempty"`
	PrinterStatus int      `json:"printer_status,omitempty"`
	Alerts        []Alert  `json:"alerts,omitempty"`
}

// ReadStatus reads and normalizes the device status.
func ReadStatus(ctx context.Context, src snmp.Source, p *profile.Profile) (StatusResult, error) {
	vals, err := src.Get(ctx, []string{OIDHrDeviceStatus, OIDHrPrinterStatus, OIDHrPrinterError})
	if err != nil {
		return StatusResult{}, err
	}
	res := StatusResult{Reasons: []string{}}
	if n, ok := vals[0].Number(); ok {
		res.DeviceStatus = int(n)
	}
	if n, ok := vals[1].Number(); ok {
		res.PrinterStatus = int(n)
	}
	var flags []string
	if vals[2].Kind == snmp.KindOctetString {
		res.ErrorBits, flags = DecodeErrorBits(vals[2].Bytes)
	}
	var lines []string
	err = src.Walk(ctx, OIDConsoleText, func(pdu snmp.PDU) error {
		if s := strings.TrimSpace(pdu.String()); s != "" {
			lines = append(lines, s)
		}
		return nil
	})
	if err != nil {
		return StatusResult{}, err
	}
	res.PanelText = strings.Join(lines, " | ")
	res.Alerts, err = readAlerts(ctx, src)
	if err != nil {
		return StatusResult{}, err
	}
	res.Status, res.Reasons = normalize(p, res, flags)
	return res, nil
}

func normalize(p *profile.Profile, r StatusResult, flags []string) (string, []string) {
	var errs, warns []string
	for _, f := range flags {
		if errorLevel[f] {
			errs = append(errs, f)
		} else {
			warns = append(warns, f)
		}
	}
	reasons := append(append([]string{}, errs...), warns...)
	if len(errs) > 0 || r.DeviceStatus == 5 {
		if len(errs) == 0 {
			reasons = append(reasons, "deviceDown")
		}
		return StatusError, reasons
	}
	if energySaving(p, r.PanelText) {
		return StatusEnergySaving, reasons
	}
	switch r.PrinterStatus {
	case 4:
		return StatusPrinting, reasons
	case 5:
		return StatusWarmup, reasons
	}
	if len(warns) > 0 || r.DeviceStatus == 3 {
		return StatusWarning, reasons
	}
	return StatusReady, reasons
}

func energySaving(p *profile.Profile, panel string) bool {
	if panel == "" || p == nil || p.Status == nil || p.Status.EnergySavingTextRegex == "" {
		return false
	}
	ok, err := p.MatchString(p.Status.EnergySavingTextRegex, panel)
	return err == nil && ok
}

func readAlerts(ctx context.Context, src snmp.Source) ([]Alert, error) {
	rows := map[string]*Alert{}
	err := src.Walk(ctx, OIDAlertTable, func(pdu snmp.PDU) error {
		rest := strings.TrimPrefix(pdu.OID, OIDAlertTable+".")
		col, idx, ok := strings.Cut(rest, ".")
		if !ok {
			return nil
		}
		a := rows[idx]
		if a == nil {
			a = &Alert{}
			if last := idx[strings.LastIndex(idx, ".")+1:]; last != "" {
				a.Index, _ = strconv.Atoi(last)
			}
			rows[idx] = a
		}
		n, _ := pdu.Number()
		switch col {
		case "2":
			a.Severity = int(n)
		case "3":
			a.TrainingLevel = int(n)
		case "4":
			a.Group = int(n)
		case "5":
			a.GroupIndex = int(n)
		case "6":
			a.Location = int(n)
		case "7":
			a.Code = int(n)
		case "8":
			a.Description = strings.TrimSpace(pdu.String())
		case "9":
			a.Time = n
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	keys := make([]string, 0, len(rows))
	for k := range rows {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return snmp.CompareOID("0."+keys[i], "0."+keys[j]) < 0 })
	out := make([]Alert, 0, len(keys))
	for _, k := range keys {
		out = append(out, *rows[k])
	}
	return out, nil
}
