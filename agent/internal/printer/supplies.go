package printer

import (
	"context"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// Supply is one row of prtMarkerSuppliesTable, normalized.
type Supply struct {
	Key         string   `json:"key"`
	Description string   `json:"description"`
	Type        string   `json:"type"`
	Class       string   `json:"class"` // consumed | receptacle | other
	Color       string   `json:"color,omitempty"`
	Level       *int64   `json:"level"`
	MaxCapacity *int64   `json:"max_capacity"`
	Percent     *float64 `json:"percent"`
	LevelState  string   `json:"level_state"` // ok | unknown | some_remaining
	Unit        string   `json:"unit,omitempty"`
	// CartridgeSerial comes from the profile's supplies.cartridge_serial_oid (PROMPT 16.3).
	CartridgeSerial string `json:"cartridge_serial,omitempty"`
}

// AttachCartridgeSerials walks a table indexed like prtMarkerSuppliesTable (hrDeviceIndex.supplyIndex)
// and sets each supply's cartridge serial.
func AttachCartridgeSerials(ctx context.Context, src snmp.Source, tableOID string, supplies []Supply) error {
	tableOID = snmp.NormalizeOID(tableOID)
	serials := map[string]string{}
	err := src.Walk(ctx, tableOID, func(pdu snmp.PDU) error {
		if v := strings.TrimSpace(pdu.String()); v != "" {
			serials[strings.TrimPrefix(pdu.OID, tableOID+".")] = v
		}
		return nil
	})
	if err != nil {
		return err
	}
	for i := range supplies {
		supplies[i].CartridgeSerial = serials[supplies[i].Key]
	}
	return nil
}

var supplyTypes = map[int64]string{
	1: "other", 2: "unknown", 3: "toner", 4: "wasteToner", 5: "ink", 6: "inkCartridge", 7: "inkRibbon",
	8: "wasteInk", 9: "opc", 10: "developer", 11: "fuserOil", 12: "solidWax", 13: "ribbonWax",
	14: "wasteWax", 15: "fuser", 16: "coronaWire", 17: "fuserOilWick", 18: "cleanerUnit",
	19: "fuserCleaningPad", 20: "transferUnit", 21: "tonerCartridge", 22: "fuserOiler", 23: "water",
	24: "wasteWater", 25: "glueWaterAdditive", 26: "wastePaper", 27: "bindingSupply",
	28: "bandingSupply", 29: "stitchingWire", 30: "shrinkWrap", 31: "paperWrap", 32: "staples",
	33: "inserts", 34: "covers",
}

var supplyUnits = map[int64]string{
	1: "other", 2: "unknown", 3: "tenThousandthsOfInches", 4: "micrometers", 7: "impressions",
	8: "sheets", 11: "hours", 12: "thousandthsOfOunces", 13: "tenthsOfGrams", 14: "hundrethsOfFluidOunces",
	15: "tenthsOfMilliliters", 16: "feet", 17: "meters", 18: "items", 19: "percent",
}

var colorPatterns = []struct {
	color string
	re    *regexp.Regexp
}{
	{"black", regexp.MustCompile(`(?i)\b(black|preto|negro|schwarz|noir|bk)\b|\(k\)|\bk\b`)},
	{"cyan", regexp.MustCompile(`(?i)\b(cyan|ciano|cian)\b|\(c\)`)},
	{"magenta", regexp.MustCompile(`(?i)\bmagenta\b|\(m\)`)},
	{"yellow", regexp.MustCompile(`(?i)\b(yellow|amarelo|amarillo|gelb|jaune)\b|\(y\)`)},
}

// NormalizeColor maps colorant names / descriptions to black|cyan|magenta|yellow ("" if unknown).
func NormalizeColor(s string) string {
	for _, cp := range colorPatterns {
		if cp.re.MatchString(s) {
			return cp.color
		}
	}
	return ""
}

// ReadSupplies reads prtMarkerSuppliesTable and the colorant names.
func ReadSupplies(ctx context.Context, src snmp.Source) ([]Supply, error) {
	colorants := map[string]string{} // "hrIdx.colorantIdx" -> nome
	err := src.Walk(ctx, OIDColorantValue, func(pdu snmp.PDU) error {
		colorants[strings.TrimPrefix(pdu.OID, OIDColorantValue+".")] = strings.TrimSpace(pdu.String())
		return nil
	})
	if err != nil {
		return nil, err
	}
	type row struct {
		cols map[string]snmp.PDU
	}
	rows := map[string]*row{}
	err = src.Walk(ctx, OIDSupplies, func(pdu snmp.PDU) error {
		rest := strings.TrimPrefix(pdu.OID, OIDSupplies+".")
		col, idx, ok := strings.Cut(rest, ".")
		if !ok {
			return nil
		}
		r := rows[idx]
		if r == nil {
			r = &row{cols: map[string]snmp.PDU{}}
			rows[idx] = r
		}
		r.cols[col] = pdu
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
	out := make([]Supply, 0, len(keys))
	for _, idx := range keys {
		out = append(out, buildSupply(idx, rows[idx].cols, colorants))
	}
	return out, nil
}

func num(cols map[string]snmp.PDU, col string) (int64, bool) {
	p, ok := cols[col]
	if !ok {
		return 0, false
	}
	return p.Number()
}

func buildSupply(idx string, cols map[string]snmp.PDU, colorants map[string]string) Supply {
	s := Supply{Key: idx, Class: "other", Type: "unknown"}
	if d, ok := cols["6"]; ok {
		s.Description = strings.TrimSpace(d.String())
	}
	if t, ok := num(cols, "5"); ok {
		if name, ok := supplyTypes[t]; ok {
			s.Type = name
		}
	}
	if c, ok := num(cols, "4"); ok {
		switch c {
		case 3:
			s.Class = "consumed"
		case 4:
			s.Class = "receptacle"
		}
	}
	if u, ok := num(cols, "7"); ok {
		s.Unit = supplyUnits[u]
	}
	hrIdx, _, _ := strings.Cut(idx, ".")
	if ci, ok := num(cols, "3"); ok && ci > 0 {
		s.Color = NormalizeColor(colorants[hrIdx+"."+strconv.FormatInt(ci, 10)])
	}
	if s.Color == "" {
		s.Color = NormalizeColor(s.Description)
	}
	level, hasLevel := num(cols, "9")
	maxCap, hasMax := num(cols, "8")
	if hasLevel {
		s.Level = &level
	}
	if hasMax {
		s.MaxCapacity = &maxCap
	}
	switch {
	case hasLevel && level == -3:
		s.LevelState = "some_remaining"
	case !hasLevel || level < 0 || !hasMax || maxCap <= 0:
		s.LevelState = "unknown"
	default:
		s.LevelState = "ok"
		pct := math.Round(float64(level)/float64(maxCap)*10000) / 100
		if pct > 100 {
			pct = 100
		}
		s.Percent = &pct
	}
	return s
}
