// Package snmp wraps gosnmp behind a small Source interface (Get/Walk) so that readers, the profile
// engine and tests can run against real devices, snmpsim or in-memory .snmprec recordings alike.
package snmp

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Kind is the SNMP value type of a PDU.
type Kind int

// Value kinds (subset of SMIv2 used by printers).
const (
	KindNoSuch Kind = iota // noSuchObject / noSuchInstance / endOfMibView / null
	KindInteger
	KindOctetString
	KindOID
	KindIPAddress
	KindCounter32
	KindGauge32
	KindTimeTicks
	KindCounter64
	KindOpaque
)

// PDU is one variable binding with a normalized value.
type PDU struct {
	OID   string // numeric, without leading dot
	Kind  Kind
	Int   int64  // numeric kinds
	Bytes []byte // OctetString / Opaque
	Text  string // OID and IpAddress values
}

// Exists reports whether the agent returned a real value for the OID.
func (p PDU) Exists() bool { return p.Kind != KindNoSuch }

// IsNumeric reports whether the value is an integer-like type.
func (p PDU) IsNumeric() bool {
	switch p.Kind {
	case KindInteger, KindCounter32, KindGauge32, KindTimeTicks, KindCounter64:
		return true
	default:
		return false
	}
}

// Number returns the numeric value; OctetStrings containing digits are accepted too
// (some printers expose counters as text).
func (p PDU) Number() (int64, bool) {
	if p.IsNumeric() {
		return p.Int, true
	}
	if p.Kind == KindOctetString {
		s := strings.TrimSpace(DecodeText(p.Bytes))
		if n, err := strconv.ParseInt(s, 10, 64); err == nil {
			return n, true
		}
	}
	return 0, false
}

// String renders the value as text (OctetStrings decoded, see DecodeText).
func (p PDU) String() string {
	switch p.Kind {
	case KindOctetString, KindOpaque:
		return DecodeText(p.Bytes)
	case KindOID, KindIPAddress:
		return p.Text
	case KindNoSuch:
		return ""
	default:
		return strconv.FormatInt(p.Int, 10)
	}
}

// Source is anything that answers SNMP GET and WALK.
type Source interface {
	// Get returns one PDU per requested OID, in order; missing OIDs come back with KindNoSuch.
	Get(ctx context.Context, oids []string) ([]PDU, error)
	// Walk calls fn for every OID strictly under root, in lexicographic OID order.
	Walk(ctx context.Context, root string, fn func(PDU) error) error
}

// ErrTimeout is returned when the device does not answer (after retries).
var ErrTimeout = errors.New("sem resposta SNMP (timeout)")

// NormalizeOID strips a leading dot and spaces.
func NormalizeOID(oid string) string {
	return strings.TrimPrefix(strings.TrimSpace(oid), ".")
}

// HasPrefix reports whether oid is equal to or below root (component-wise).
func HasPrefix(oid, root string) bool {
	oid, root = NormalizeOID(oid), NormalizeOID(root)
	return oid == root || strings.HasPrefix(oid, root+".")
}

// CompareOID orders numeric OIDs component by component.
func CompareOID(a, b string) int {
	pa, pb := strings.Split(NormalizeOID(a), "."), strings.Split(NormalizeOID(b), ".")
	for i := 0; i < len(pa) && i < len(pb); i++ {
		na, errA := strconv.ParseUint(pa[i], 10, 64)
		nb, errB := strconv.ParseUint(pb[i], 10, 64)
		if errA != nil || errB != nil {
			if c := strings.Compare(pa[i], pb[i]); c != 0 {
				return c
			}
			continue
		}
		if na != nb {
			if na < nb {
				return -1
			}
			return 1
		}
	}
	switch {
	case len(pa) < len(pb):
		return -1
	case len(pa) > len(pb):
		return 1
	default:
		return 0
	}
}

// ValidOID reports whether s is a dotted numeric OID with at least two arcs.
func ValidOID(s string) bool {
	parts := strings.Split(NormalizeOID(s), ".")
	if len(parts) < 2 {
		return false
	}
	for _, p := range parts {
		if p == "" {
			return false
		}
		for _, r := range p {
			if r < '0' || r > '9' {
				return false
			}
		}
	}
	return true
}

// DecodeText turns an OctetString into text. Printers commonly return names either as plain bytes,
// as UTF-8/Latin-1, NUL-padded, or as a *textual* hex dump ("54 6F 74 61 6C"); all become plain text.
func DecodeText(b []byte) string {
	b = trimNUL(b)
	if s, ok := decodeHexDump(string(b)); ok {
		return s
	}
	if utf8.Valid(b) {
		return string(b)
	}
	runes := make([]rune, len(b))
	for i, c := range b {
		runes[i] = rune(c) // Latin-1
	}
	return string(runes)
}

func trimNUL(b []byte) []byte {
	for len(b) > 0 && b[len(b)-1] == 0 {
		b = b[:len(b)-1]
	}
	return b
}

// decodeHexDump decodes strings like "54 6F 74 61 6C 20 31" or "0x546F74616C" when the result is
// printable text; anything else is left alone.
func decodeHexDump(s string) (string, bool) {
	t := strings.TrimSpace(s)
	t = strings.TrimPrefix(strings.TrimPrefix(t, "0x"), "0X")
	t = strings.NewReplacer(" ", "", ":", "", "-", "").Replace(t)
	if len(t) < 4 || len(t)%2 != 0 || len(s) < 4 {
		return "", false
	}
	// Exige separadores ou prefixo 0x, para não confundir texto como "CAFE" com hex.
	if !strings.ContainsAny(s, " :-") && !strings.HasPrefix(strings.TrimSpace(s), "0x") {
		return "", false
	}
	out := make([]byte, len(t)/2)
	for i := 0; i < len(t); i += 2 {
		v, err := strconv.ParseUint(t[i:i+2], 16, 8)
		if err != nil {
			return "", false
		}
		out[i/2] = byte(v)
	}
	out = trimNUL(out)
	if !utf8.Valid(out) {
		return "", false
	}
	for _, r := range string(out) {
		if !unicode.IsPrint(r) && r != '\t' {
			return "", false
		}
	}
	return string(out), true
}

// FormatMAC renders 6 raw bytes as AA:BB:CC:DD:EE:FF; returns "" for anything else or an all-zero MAC.
func FormatMAC(b []byte) string {
	if len(b) != 6 {
		return ""
	}
	zero := true
	for _, c := range b {
		if c != 0 {
			zero = false
		}
	}
	if zero {
		return ""
	}
	return fmt.Sprintf("%02X:%02X:%02X:%02X:%02X:%02X", b[0], b[1], b[2], b[3], b[4], b[5])
}
