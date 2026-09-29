package printer

import (
	"context"
	"sort"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// Standard OIDs of the daily attributes (PROMPT 6.2 / 16.8).
const (
	OIDHrMemorySize          = "1.3.6.1.2.1.25.2.2.0"
	OIDHrStorageTable        = "1.3.6.1.2.1.25.2.3.1"
	OIDHrDeviceTable         = "1.3.6.1.2.1.25.3.2.1"
	OIDEntPhysicalFirmwareRv = "1.3.6.1.2.1.47.1.1.1.1.9"
	OIDEntPhysicalSoftwareRv = "1.3.6.1.2.1.47.1.1.1.1.10"
	// HrStorageFixedDisk is hrStorageType of a fixed disk.
	HrStorageFixedDisk = "1.3.6.1.2.1.25.2.1.4"
	hrDeviceTypePrefix = "1.3.6.1.2.1.25.3.1."
)

// hrDeviceTypes names the standard hrDeviceType OIDs (1.3.6.1.2.1.25.3.1.N).
var hrDeviceTypes = map[string]string{
	"1": "other", "2": "unknown", "3": "processor", "4": "network", "5": "printer", "6": "disk_storage",
	"10": "video", "11": "audio", "12": "coprocessor", "13": "keyboard", "14": "modem", "15": "parallel_port",
	"16": "pointing", "17": "serial_port", "18": "tape", "19": "clock", "20": "volatile_memory",
	"21": "non_volatile_memory",
}

// hrDeviceStatus values (1 unknown, 2 running, 3 warning, 4 testing, 5 down).
var hrDeviceStatus = map[int64]string{1: "unknown", 2: "running", 3: "warning", 4: "testing", 5: "down"}

// Storage is one fixed disk of the device.
type Storage struct {
	Description string `json:"description"`
	SizeBytes   int64  `json:"size_bytes"`
	UsedBytes   int64  `json:"used_bytes"`
}

// Subsystem is one hrDevice row, or a profile-provided subsystem (printer/copier/scanner).
type Subsystem struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Status      string `json:"status"`
}

// Part is a part level/counter read through the profile.
type Part struct {
	Name  string `json:"name"`
	Part  string `json:"part"`
	Color string `json:"color,omitempty"`
	Unit  string `json:"unit"`
	Value int64  `json:"value"`
}

// Attributes are the daily attributes of a device (PROMPT 16.8).
type Attributes struct {
	Firmware      []string    `json:"firmware,omitempty"`
	MemoryBytes   *int64      `json:"memory_bytes,omitempty"`
	Storage       []Storage   `json:"storage,omitempty"`
	MAC           string      `json:"mac,omitempty"`
	SSID          string      `json:"ssid,omitempty"`
	UptimeSeconds *int64      `json:"uptime_seconds,omitempty"`
	Subsystems    []Subsystem `json:"subsystems,omitempty"`
	PanelText     string      `json:"panel_text,omitempty"`
	SysLocation   string      `json:"sys_location,omitempty"`
	Parts         []Part      `json:"parts,omitempty"`
}

// ReadAttributes reads the standard attributes plus what the profile provides. `identity` supplies the
// values already read (MAC, sysLocation, firmware from the profile's identity list).
func ReadAttributes(ctx context.Context, src snmp.Source, p *profile.Profile, identity Identity) (Attributes, error) {
	a := Attributes{MAC: identity.MAC, SysLocation: identity.SysLocation}
	vals, err := src.Get(ctx, []string{OIDHrMemorySize, OIDSysUpTime})
	if err != nil {
		return Attributes{}, err
	}
	if n, ok := vals[0].Number(); ok && vals[0].Exists() && n > 0 {
		b := n * 1024 // hrMemorySize em KBytes
		a.MemoryBytes = &b
	}
	if n, ok := vals[1].Number(); ok && vals[1].Exists() && n >= 0 {
		s := n / 100 // TimeTicks: centésimos de segundo
		a.UptimeSeconds = &s
	}
	if a.Firmware, err = readFirmware(ctx, src, identity.Firmware); err != nil {
		return Attributes{}, err
	}
	if a.Storage, err = readStorage(ctx, src); err != nil {
		return Attributes{}, err
	}
	if a.Subsystems, err = readHrDevices(ctx, src); err != nil {
		return Attributes{}, err
	}
	var panel []string
	err = src.Walk(ctx, OIDConsoleText, func(pdu snmp.PDU) error {
		if s := strings.TrimSpace(pdu.String()); s != "" {
			panel = append(panel, s)
		}
		return nil
	})
	if err != nil {
		return Attributes{}, err
	}
	a.PanelText = strings.Join(panel, " | ")
	if p != nil && p.Attributes != nil {
		if err := readProfileAttributes(ctx, src, p.Attributes, &a); err != nil {
			return Attributes{}, err
		}
	}
	return a, nil
}

// readFirmware collects the identity firmware plus ENTITY-MIB firmware/software revisions, de-duplicated.
func readFirmware(ctx context.Context, src snmp.Source, identityFW string) ([]string, error) {
	seen := map[string]bool{}
	var out []string
	add := func(v string) {
		v = strings.TrimSpace(v)
		if v != "" && !seen[v] {
			seen[v] = true
			out = append(out, v)
		}
	}
	add(identityFW)
	for _, oid := range []string{OIDEntPhysicalFirmwareRv, OIDEntPhysicalSoftwareRv} {
		err := src.Walk(ctx, oid, func(pdu snmp.PDU) error {
			add(pdu.String())
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	return out, nil
}

func tableRows(ctx context.Context, src snmp.Source, table string) (map[string]map[string]snmp.PDU, []string, error) {
	rows := map[string]map[string]snmp.PDU{}
	err := src.Walk(ctx, table, func(pdu snmp.PDU) error {
		col, idx, ok := strings.Cut(strings.TrimPrefix(pdu.OID, table+"."), ".")
		if !ok {
			return nil
		}
		if rows[idx] == nil {
			rows[idx] = map[string]snmp.PDU{}
		}
		rows[idx][col] = pdu
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	keys := make([]string, 0, len(rows))
	for k := range rows {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return snmp.CompareOID("0."+keys[i], "0."+keys[j]) < 0 })
	return rows, keys, nil
}

// readStorage lists the fixed disks of hrStorageTable (size and used in bytes).
func readStorage(ctx context.Context, src snmp.Source) ([]Storage, error) {
	rows, keys, err := tableRows(ctx, src, OIDHrStorageTable)
	if err != nil {
		return nil, err
	}
	var out []Storage
	for _, k := range keys {
		cols := rows[k]
		if snmp.NormalizeOID(cols["2"].Text) != HrStorageFixedDisk {
			continue
		}
		unit, _ := num(cols, "4")
		size, _ := num(cols, "5")
		used, _ := num(cols, "6")
		if unit <= 0 || size <= 0 {
			continue
		}
		out = append(out, Storage{
			Description: strings.TrimSpace(cols["3"].String()), SizeBytes: size * unit, UsedBytes: used * unit,
		})
	}
	return out, nil
}

// readHrDevices lists hrDeviceTable rows (type, description, status) as subsystems.
func readHrDevices(ctx context.Context, src snmp.Source) ([]Subsystem, error) {
	rows, keys, err := tableRows(ctx, src, OIDHrDeviceTable)
	if err != nil {
		return nil, err
	}
	var out []Subsystem
	for _, k := range keys {
		cols := rows[k]
		typ := strings.TrimPrefix(snmp.NormalizeOID(cols["2"].Text), hrDeviceTypePrefix)
		name, ok := hrDeviceTypes[typ]
		if !ok {
			name = "other"
		}
		status := "unknown"
		if n, ok := num(cols, "5"); ok {
			if s, ok := hrDeviceStatus[n]; ok {
				status = s
			}
		}
		out = append(out, Subsystem{Name: name, Description: strings.TrimSpace(cols["3"].String()), Status: status})
	}
	return out, nil
}

func usableOID(oid string) bool { return oid != "" && oid != profile.Placeholder }

// firstText returns the first non-empty value of an ordered OID list.
func firstText(ctx context.Context, src snmp.Source, refs []profile.OIDRef) (string, error) {
	var oids []string
	for _, r := range refs {
		if usableOID(r.OID) {
			oids = append(oids, snmp.NormalizeOID(r.OID))
		}
	}
	if len(oids) == 0 {
		return "", nil
	}
	vals, err := src.Get(ctx, oids)
	if err != nil {
		return "", err
	}
	for _, v := range vals {
		if s := strings.TrimSpace(v.String()); v.Exists() && s != "" {
			return s, nil
		}
	}
	return "", nil
}

func readProfileAttributes(ctx context.Context, src snmp.Source, pa *profile.Attributes, a *Attributes) error {
	ssid, err := firstText(ctx, src, pa.SSID)
	if err != nil {
		return err
	}
	a.SSID = ssid
	if pa.Subsystems != nil {
		for _, sub := range []struct {
			name string
			refs []profile.OIDRef
		}{{"printer", pa.Subsystems.Printer}, {"copier", pa.Subsystems.Copier}, {"scanner", pa.Subsystems.Scanner}} {
			status, err := firstText(ctx, src, sub.refs)
			if err != nil {
				return err
			}
			if status != "" {
				a.Subsystems = append(a.Subsystems, Subsystem{Name: sub.name, Status: status})
			}
		}
	}
	names := make([]string, 0, len(pa.Parts))
	for n, part := range pa.Parts {
		if usableOID(part.OID) {
			names = append(names, n)
		}
	}
	sort.Strings(names)
	if len(names) == 0 {
		return nil
	}
	oids := make([]string, len(names))
	for i, n := range names {
		oids[i] = snmp.NormalizeOID(pa.Parts[n].OID)
	}
	vals, err := src.Get(ctx, oids)
	if err != nil {
		return err
	}
	for i, n := range names {
		if v, ok := vals[i].Number(); ok && vals[i].Exists() {
			part := pa.Parts[n]
			a.Parts = append(a.Parts, Part{Name: n, Part: part.Part, Color: part.Color, Unit: part.Unit, Value: v})
		}
	}
	return nil
}
