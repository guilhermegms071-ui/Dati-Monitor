package snmp

import (
	"bufio"
	"context"
	"encoding/hex"
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
)

// snmprec type tags (snmpsim format "OID|TYPE|VALUE").
const (
	tagInteger   = "2"
	tagOctet     = "4"
	tagOctetHex  = "4x"
	tagNull      = "5"
	tagOID       = "6"
	tagIP        = "64"
	tagCounter32 = "65"
	tagGauge32   = "66"
	tagTimeTicks = "67"
	tagOpaque    = "68"
	tagCounter64 = "70"
)

// ParseSnmprec reads an snmpsim .snmprec recording. Variation-module tags (e.g. "4:delay") are rejected.
func ParseSnmprec(r io.Reader) ([]PDU, error) {
	var out []PDU
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	line := 0
	for sc.Scan() {
		line++
		text := strings.TrimRight(sc.Text(), "\r")
		if strings.TrimSpace(text) == "" || strings.HasPrefix(text, "#") {
			continue
		}
		parts := strings.SplitN(text, "|", 3)
		if len(parts) != 3 {
			return nil, fmt.Errorf("linha %d: formato inválido (esperado OID|TIPO|VALOR)", line)
		}
		pdu, err := parseRecord(parts[0], parts[1], parts[2])
		if err != nil {
			return nil, fmt.Errorf("linha %d: %w", line, err)
		}
		out = append(out, pdu)
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return out, nil
}

func parseRecord(oid, tag, value string) (PDU, error) {
	oid = NormalizeOID(oid)
	if !ValidOID(oid) {
		return PDU{}, fmt.Errorf("OID inválido %q", oid)
	}
	p := PDU{OID: oid}
	var err error
	switch tag {
	case tagInteger, tagCounter32, tagGauge32, tagTimeTicks, tagCounter64:
		p.Kind = map[string]Kind{
			tagInteger: KindInteger, tagCounter32: KindCounter32, tagGauge32: KindGauge32,
			tagTimeTicks: KindTimeTicks, tagCounter64: KindCounter64,
		}[tag]
		p.Int, err = strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	case tagOctet:
		p.Kind, p.Bytes = KindOctetString, []byte(value)
	case tagOctetHex:
		p.Kind = KindOctetString
		p.Bytes, err = hex.DecodeString(strings.TrimSpace(value))
	case tagOpaque:
		p.Kind = KindOpaque
		p.Bytes, err = hex.DecodeString(strings.TrimSpace(value))
	case tagOID:
		p.Kind, p.Text = KindOID, NormalizeOID(value)
	case tagIP:
		p.Kind, p.Text = KindIPAddress, strings.TrimSpace(value)
	case tagNull:
		p.Kind = KindNoSuch
	default:
		return PDU{}, fmt.Errorf("tipo %q não suportado", tag)
	}
	if err != nil {
		return PDU{}, fmt.Errorf("valor inválido para tipo %s: %w", tag, err)
	}
	return p, nil
}

// WriteSnmprec writes PDUs in snmpsim format. OctetStrings that are printable single-line text are
// written as type 4, anything else as 4x (hex) so the recording round-trips byte-exact.
func WriteSnmprec(w io.Writer, pdus []PDU) error {
	bw := bufio.NewWriter(w)
	for _, p := range pdus {
		tag, value, ok := recordFor(p)
		if !ok {
			continue
		}
		if _, err := fmt.Fprintf(bw, "%s|%s|%s\n", p.OID, tag, value); err != nil {
			return err
		}
	}
	return bw.Flush()
}

func recordFor(p PDU) (tag, value string, ok bool) {
	switch p.Kind {
	case KindInteger:
		return tagInteger, strconv.FormatInt(p.Int, 10), true
	case KindCounter32:
		return tagCounter32, strconv.FormatInt(p.Int, 10), true
	case KindGauge32:
		return tagGauge32, strconv.FormatInt(p.Int, 10), true
	case KindTimeTicks:
		return tagTimeTicks, strconv.FormatInt(p.Int, 10), true
	case KindCounter64:
		return tagCounter64, strconv.FormatInt(p.Int, 10), true
	case KindOctetString:
		if isPlainText(p.Bytes) {
			return tagOctet, string(p.Bytes), true
		}
		return tagOctetHex, hex.EncodeToString(p.Bytes), true
	case KindOpaque:
		return tagOpaque, hex.EncodeToString(p.Bytes), true
	case KindOID:
		return tagOID, p.Text, true
	case KindIPAddress:
		return tagIP, p.Text, true
	default:
		return "", "", false
	}
}

func isPlainText(b []byte) bool {
	if len(b) == 0 {
		return true
	}
	for _, c := range b {
		if c < 0x20 || c > 0x7e || c == '|' {
			return false
		}
	}
	return true
}

// MemSource is an in-memory Source built from a recording (used by tests and profile dry-runs).
type MemSource struct {
	pdus  []PDU
	index map[string]int
}

// NewMemSource sorts the PDUs by OID and indexes them.
func NewMemSource(pdus []PDU) *MemSource {
	sorted := make([]PDU, len(pdus))
	copy(sorted, pdus)
	sort.SliceStable(sorted, func(i, j int) bool { return CompareOID(sorted[i].OID, sorted[j].OID) < 0 })
	idx := make(map[string]int, len(sorted))
	for i, p := range sorted {
		idx[p.OID] = i
	}
	return &MemSource{pdus: sorted, index: idx}
}

// Get implements Source.
func (m *MemSource) Get(_ context.Context, oids []string) ([]PDU, error) {
	out := make([]PDU, len(oids))
	for i, oid := range oids {
		oid = NormalizeOID(oid)
		if j, ok := m.index[oid]; ok {
			out[i] = m.pdus[j]
		} else {
			out[i] = PDU{OID: oid, Kind: KindNoSuch}
		}
	}
	return out, nil
}

// Walk implements Source.
func (m *MemSource) Walk(ctx context.Context, root string, fn func(PDU) error) error {
	root = NormalizeOID(root)
	start := sort.Search(len(m.pdus), func(i int) bool { return CompareOID(m.pdus[i].OID, root) > 0 })
	for i := start; i < len(m.pdus); i++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		if !HasPrefix(m.pdus[i].OID, root) {
			break
		}
		if err := fn(m.pdus[i]); err != nil {
			return err
		}
	}
	return nil
}

// Len returns how many PDUs the source holds.
func (m *MemSource) Len() int { return len(m.pdus) }
