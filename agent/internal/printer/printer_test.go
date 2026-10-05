package printer

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

func sim(t *testing.T, name string) *snmp.MemSource {
	t.Helper()
	f, err := os.Open(filepath.Join("../../../profiles/recordings/sim", name, "public.snmprec"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	pdus, err := snmp.ParseSnmprec(f)
	if err != nil {
		t.Fatal(err)
	}
	return snmp.NewMemSource(pdus)
}

func profiles(t *testing.T) []*profile.Profile {
	t.Helper()
	ps, err := profile.LoadDir("../../../profiles")
	if err != nil {
		t.Fatal(err)
	}
	return ps
}

func TestDecodeErrorBitsMSBFirst(t *testing.T) {
	cases := []struct {
		in    []byte
		mask  int
		names []string
	}{
		{[]byte{0x00, 0x00}, 0, nil},
		{[]byte{0x80}, 1 << 0, []string{"lowPaper"}},
		{[]byte{0x0C, 0x00}, 1<<4 | 1<<5, []string{"doorOpen", "jammed"}},
		{[]byte{0x01, 0x80}, 1<<7 | 1<<8, []string{"serviceRequested", "inputTrayMissing"}},
		{[]byte{0x00, 0x02}, 1 << 14, []string{"overduePreventMaint"}},
		{[]byte{0x00, 0x01}, 0, nil}, // bit 15 não definido na RFC: ignorado
		{nil, 0, nil},
	}
	for _, c := range cases {
		mask, names := DecodeErrorBits(c.in)
		if mask != c.mask || !reflect.DeepEqual(names, c.names) {
			t.Errorf("%x: got %d %v, want %d %v", c.in, mask, names, c.mask, c.names)
		}
	}
}

func TestStatusOfSimulatedPrinters(t *testing.T) {
	ps := profiles(t)
	cases := []struct {
		sim, profile, status string
		reasons              []string
		alerts               int
	}{
		{"01-canon-cor", "canon", StatusReady, []string{}, 0},
		{"05-generica", "generic", StatusReady, []string{}, 0},
		{"06-economia", "generic", StatusEnergySaving, []string{}, 0},
		{"07-erros", "generic", StatusError, []string{"doorOpen", "jammed"}, 2},
	}
	for _, c := range cases {
		t.Run(c.sim, func(t *testing.T) {
			src := sim(t, c.sim)
			var p *profile.Profile
			for _, x := range ps {
				if x.ID == c.profile {
					p = x
				}
			}
			st, err := ReadStatus(context.Background(), src, p)
			if err != nil {
				t.Fatal(err)
			}
			if st.Status != c.status || !reflect.DeepEqual(st.Reasons, c.reasons) || len(st.Alerts) != c.alerts {
				t.Fatalf("%+v", st)
			}
		})
	}
	st, err := ReadStatus(context.Background(), sim(t, "07-erros"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if st.ErrorBits != 1<<4|1<<5 || st.Alerts[0].Description != "Atolamento de papel na bandeja 2" || st.Alerts[0].Code != 8 {
		t.Fatalf("%+v", st)
	}
}

// Walks da rede real (Fase 10): Konicas com manutenção pedida/papel baixo/preventiva vencida são
// "Atenção" (elas mesmas dizem hrDeviceStatus=warning), a Kyocera pronta é "Pronta".
func TestStatusOfRealPrinters(t *testing.T) {
	cases := map[string]struct {
		status  string
		reasons []string
	}{
		"konica_accurioprint_c4065": {StatusWarning, []string{"lowPaper", "serviceRequested"}},
		"konica_bizhub_c287":        {StatusWarning, []string{"serviceRequested"}},
		"konica_bizhub_c454e":       {StatusWarning, []string{"serviceRequested", "overduePreventMaint"}},
		"kyocera_ecosys_m3550idn":   {StatusReady, []string{}},
	}
	for name, want := range cases {
		t.Run(name, func(t *testing.T) {
			f, err := os.Open(filepath.Join("../../../profiles/recordings/real", name+".snmprec"))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = f.Close() }()
			pdus, err := snmp.ParseSnmprec(f)
			if err != nil {
				t.Fatal(err)
			}
			st, err := ReadStatus(context.Background(), snmp.NewMemSource(pdus), nil)
			if err != nil {
				t.Fatal(err)
			}
			if st.Status != want.status || !reflect.DeepEqual(st.Reasons, want.reasons) {
				t.Fatalf("status %s %v, esperado %s %v", st.Status, st.Reasons, want.status, want.reasons)
			}
		})
	}
}

func TestNormalizePrecedence(t *testing.T) {
	sleepy := &profile.Profile{ID: "p", Version: 1, Status: &profile.Status{EnergySavingTextRegex: "(?i)sleep"}}
	cases := []struct {
		r      StatusResult
		flags  []string
		p      *profile.Profile
		status string
	}{
		{StatusResult{DeviceStatus: 5}, nil, nil, StatusError},
		{StatusResult{DeviceStatus: 2, PrinterStatus: 4}, nil, nil, StatusPrinting},
		{StatusResult{DeviceStatus: 2, PrinterStatus: 5}, nil, nil, StatusWarmup},
		{StatusResult{DeviceStatus: 3}, nil, nil, StatusWarning},
		{StatusResult{DeviceStatus: 2}, []string{"lowToner"}, nil, StatusWarning},
		{StatusResult{DeviceStatus: 2, PanelText: "Sleep"}, []string{"lowToner"}, sleepy, StatusEnergySaving},
		{StatusResult{DeviceStatus: 2, PanelText: "Sleep"}, []string{"jammed"}, sleepy, StatusError},
		{StatusResult{DeviceStatus: 2}, nil, nil, StatusReady},
		{StatusResult{DeviceStatus: 3}, []string{"serviceRequested", "overduePreventMaint"}, nil, StatusWarning},
		{StatusResult{DeviceStatus: 3}, []string{"serviceRequested", "noToner"}, nil, StatusError},
	}
	for i, c := range cases {
		if got, _ := normalize(c.p, c.r, c.flags); got != c.status {
			t.Errorf("caso %d: got %s want %s", i, got, c.status)
		}
	}
}

func TestSuppliesOfSimulatedPrinters(t *testing.T) {
	sup, err := ReadSupplies(context.Background(), sim(t, "01-canon-cor"))
	if err != nil {
		t.Fatal(err)
	}
	if len(sup) != 5 {
		t.Fatalf("esperava 5 suprimentos, veio %d", len(sup))
	}
	want := []struct {
		color, class, typ string
		pct               float64
	}{
		{"black", "consumed", "toner", 45}, {"cyan", "consumed", "toner", 80}, {"magenta", "consumed", "toner", 12},
		{"yellow", "consumed", "toner", 60}, {"", "receptacle", "wasteToner", 30},
	}
	for i, w := range want {
		s := sup[i]
		if s.Color != w.color || s.Class != w.class || s.Type != w.typ || s.Percent == nil || *s.Percent != w.pct {
			t.Errorf("suprimento %d: %+v (pct=%v)", i, s, s.Percent)
		}
		if s.LevelState != "ok" || s.Unit != "percent" || s.Key != "1."+string(rune('1'+i)) {
			t.Errorf("suprimento %d: %+v", i, s)
		}
	}
	errSup, err := ReadSupplies(context.Background(), sim(t, "07-erros"))
	if err != nil {
		t.Fatal(err)
	}
	if errSup[0].LevelState != "some_remaining" || errSup[0].Percent != nil || *errSup[0].Level != -3 {
		t.Fatalf("%+v", errSup[0])
	}
	km, err := ReadSupplies(context.Background(), sim(t, "03-konica-cor"))
	if err != nil {
		t.Fatal(err)
	}
	if km[4].Type != "opc" || km[4].Color != "black" || *km[4].Percent != 70 {
		t.Fatalf("%+v", km[4])
	}
}

func TestBuildSupplyEdgeCases(t *testing.T) {
	n := func(col string, v int64) snmp.PDU { return snmp.PDU{OID: col, Kind: snmp.KindInteger, Int: v} }
	cols := map[string]snmp.PDU{"9": n("9", -2), "8": n("8", 100), "6": {Kind: snmp.KindOctetString, Bytes: []byte("Toner Amarelo")}}
	s := buildSupply("1.9", cols, nil)
	if s.LevelState != "unknown" || s.Color != "yellow" || s.Percent != nil {
		t.Fatalf("%+v", s)
	}
	over := buildSupply("1.1", map[string]snmp.PDU{"9": n("9", 150), "8": n("8", 100)}, nil)
	if *over.Percent != 100 {
		t.Fatalf("%+v", over)
	}
	noMax := buildSupply("1.2", map[string]snmp.PDU{"9": n("9", 10), "8": n("8", 0)}, nil)
	if noMax.LevelState != "unknown" {
		t.Fatalf("%+v", noMax)
	}
	for in, want := range map[string]string{"Preto": "black", "Toner (K)": "black", "Ciano": "cyan", "Magenta X": "magenta", "Fusor": ""} {
		if got := NormalizeColor(in); got != want {
			t.Errorf("%q: %q want %q", in, got, want)
		}
	}
}

func TestProbeAndIdentity(t *testing.T) {
	ps := profiles(t)
	ctx := context.Background()
	pr, err := ReadProbe(ctx, sim(t, "03-konica-cor"))
	if err != nil {
		t.Fatal(err)
	}
	if !pr.IsPrinter() || pr.Serial != "A797019500624" || *pr.LifeCount != 217031 {
		t.Fatalf("%+v", pr)
	}
	if (Probe{}).IsPrinter() {
		t.Fatal("probe vazio não é impressora")
	}
	if !(Probe{Serial: "x"}).IsPrinter() {
		t.Fatal("serial basta")
	}
	id, p, err := ReadIdentity(ctx, sim(t, "02-canon-pb"), ps)
	if err != nil {
		t.Fatal(err)
	}
	want := Identity{
		Serial: "SIMCAN0002", Model: "iR 1643i", SysObjectID: "1.3.6.1.4.1.1602.4.9", SysDescr: "Canon iR 1643i",
		SysName: "CANON-PB-02", SysLocation: "Simulador Dati Monitor", MAC: "00:AA:00:00:00:02", ProfileKey: "canon",
	}
	if id != want || p.ID != "canon" {
		t.Fatalf("got %+v", id)
	}
}
