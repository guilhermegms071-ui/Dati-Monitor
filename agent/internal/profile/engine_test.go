package profile

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

const (
	profilesDir = "../../../profiles"
	simDir      = "../../../profiles/recordings/sim"
)

func loadProfiles(t *testing.T) []*Profile {
	t.Helper()
	ps, err := LoadDir(profilesDir)
	if err != nil {
		t.Fatal(err)
	}
	return ps
}

func loadSim(t *testing.T, name string) *snmp.MemSource {
	t.Helper()
	f, err := os.Open(filepath.Join(simDir, name, "public.snmprec"))
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

func sysObjectID(t *testing.T, src snmp.Source) string {
	t.Helper()
	v, err := src.Get(context.Background(), []string{"1.3.6.1.2.1.1.2.0"})
	if err != nil {
		t.Fatal(err)
	}
	return v[0].Text
}

type expectation struct {
	sim        string
	profile    string
	source     string
	serial     string
	model      string
	firmware   string
	monoOnly   bool
	counters   map[string]int64
	unresolved []string
	extraKey   string
}

func TestEngineAgainstSimulatedPrinters(t *testing.T) {
	profiles := loadProfiles(t)
	cases := []expectation{
		{
			sim: "01-canon-cor", profile: "canon", source: "canon_id_table", serial: "SIMCAN0001",
			model: "iR-ADV C5540", firmware: "65.23",
			counters: map[string]int64{
				"total": 150000, "mono": 90000, "color": 60000, "mono_large": 5000, "mono_small": 85000,
				"color_large": 10000, "color_small": 50000, "print_total": 120000, "scan": 33333,
			},
			unresolved: []string{"color_2", "mono_2", "total_2"}, extraKey: "canon_id_table",
		},
		{
			sim: "02-canon-pb", profile: "canon", source: "canon_named_table", serial: "SIMCAN0002",
			model: "iR 1643i", monoOnly: true,
			counters: map[string]int64{
				"total": 45678, "mono": 45678, "color": 0, "print_total": 30000, "copy_total": 15678, "scan": 2000,
			},
			extraKey: "canon_named_table",
		},
		{
			// Caso real conferido com o Datacount: 217031 = 100150 + 116881.
			sim: "03-konica-cor", profile: "konica-minolta", source: "konica_counters", serial: "A797019500624",
			model: "bizhub C287", firmware: "G00-R5",
			counters: map[string]int64{
				"total": 217031, "mono": 100150, "color": 116881, "copy_mono": 40150, "print_mono": 60000,
				"copy_color": 16881, "print_color": 100000, "duplex": 5000, "scan": 7777,
			},
			extraKey: "konica_counters",
		},
		{
			sim: "04-konica-pb", profile: "konica-minolta", source: "konica_counters", serial: "SIMKM0004",
			model: "bizhub 367", firmware: "G10-R2", monoOnly: true,
			counters:   map[string]int64{"total": 88000, "mono": 88000, "color": 0, "copy_mono": 30000, "print_mono": 58000},
			unresolved: []string{"copy_color", "duplex", "print_color", "scan"}, extraKey: "konica_counters",
		},
		{
			sim: "05-generica", profile: "generic", source: "standard", serial: "SIMGEN0005",
			model: "Generic Laser Printer 5000", counters: map[string]int64{"total": 48213},
		},
	}
	for _, tc := range cases {
		t.Run(tc.sim, func(t *testing.T) {
			ctx := context.Background()
			src := loadSim(t, tc.sim)
			first := Select(profiles, sysObjectID(t, src), "")
			id, err := ResolveIdentity(ctx, src, first)
			if err != nil {
				t.Fatal(err)
			}
			p := Select(profiles, sysObjectID(t, src), id.Model)
			if p.ID != tc.profile {
				t.Fatalf("perfil: got %s want %s", p.ID, tc.profile)
			}
			if id.Serial != tc.serial || id.Model != tc.model || id.Firmware != tc.firmware {
				t.Fatalf("identidade: %+v", id)
			}
			res, err := Evaluate(ctx, src, p, id.Model)
			if err != nil {
				t.Fatal(err)
			}
			if res.Source != tc.source || res.MonoOnly != tc.monoOnly {
				t.Fatalf("fonte=%s monoOnly=%v", res.Source, res.MonoOnly)
			}
			for k, want := range tc.counters {
				if got, ok := res.Counters[k]; !ok || got != want {
					t.Errorf("%s: got %d (ok=%v) want %d", k, got, ok, want)
				}
			}
			if strings.Join(res.Unresolved, ",") != strings.Join(tc.unresolved, ",") {
				t.Errorf("não resolvidos: got %v want %v", res.Unresolved, tc.unresolved)
			}
			if tc.extraKey != "" {
				rows, ok := res.Extra[tc.extraKey].(map[string]any)
				if !ok || len(rows) == 0 {
					t.Errorf("extra[%s] vazio: %#v", tc.extraKey, res.Extra)
				}
			}
			if res.SumTolerancePercent != 2 {
				t.Errorf("tolerância: %v", res.SumTolerancePercent)
			}
		})
	}
}

func TestCanonIDTableRowsGoToExtra(t *testing.T) {
	profiles := loadProfiles(t)
	src := loadSim(t, "01-canon-cor")
	p := Select(profiles, sysObjectID(t, src), "iR-ADV C5540")
	res, err := Evaluate(context.Background(), src, p, "iR-ADV C5540")
	if err != nil {
		t.Fatal(err)
	}
	rows := res.Extra["canon_id_table"].(map[string]any)
	for _, id := range []string{"101", "108", "112", "113", "122", "123", "301", "501"} {
		if _, ok := rows[id]; !ok {
			t.Errorf("ID %s ausente em extra: %v", id, rows)
		}
	}
	if len(rows) != 8 {
		t.Errorf("esperava 8 linhas, veio %d", len(rows))
	}
}

func TestStandardFallbackFillsTotal(t *testing.T) {
	// Tabela A existe, mas sem o ID 101: o total vem da fonte "standard".
	pdus := []snmp.PDU{
		{OID: "1.3.6.1.4.1.1602.1.11.1.3.1.4.108", Kind: snmp.KindCounter32, Int: 10},
		{OID: OIDLifeCount, Kind: snmp.KindCounter32, Int: 999},
	}
	profiles := loadProfiles(t)
	p := Select(profiles, "1.3.6.1.4.1.1602.4.1", "")
	res, err := Evaluate(context.Background(), snmp.NewMemSource(pdus), p, "")
	if err != nil {
		t.Fatal(err)
	}
	if res.Counters["total"] != 999 || res.Extra["total_from"] != "standard" || res.Counters["mono"] != 10 {
		t.Fatalf("%+v", res)
	}
}

func TestNoSourceDetectedAndNilProfile(t *testing.T) {
	p := &Profile{ID: "x", Version: 1, CounterSources: []CounterSource{{
		Name: "a", DetectOID: "1.2.3", Counters: map[string]Counter{"total": {OID: "1.2.3.1"}},
	}}}
	res, err := Evaluate(context.Background(), snmp.NewMemSource(nil), p, "")
	if err != nil {
		t.Fatal(err)
	}
	if res.Source != "" || len(res.Unresolved) != 1 {
		t.Fatalf("%+v", res)
	}
	if _, err := Evaluate(context.Background(), snmp.NewMemSource(nil), nil, ""); err == nil {
		t.Fatal("esperava erro sem perfil")
	}
}

func TestSumRequiresAllPartsAndFirstOfFallsThrough(t *testing.T) {
	pdus := []snmp.PDU{
		{OID: "1.1.1", Kind: snmp.KindCounter32, Int: 5},
		{OID: "1.1.3", Kind: snmp.KindOctetString, Bytes: []byte(" 42 ")},
	}
	p := &Profile{ID: "t", Version: 1, Counters: map[string]Counter{
		"partial":  {Sum: []string{"1.1.1", "1.1.2"}},
		"fallback": {FirstOf: []Counter{{OID: "1.1.2"}, {Sum: []string{"1.1.1", "1.1.3"}}}},
		"text":     {OID: "1.1.3"},
		"holder":   {OID: Placeholder},
		"calc":     {Expr: "fallback * 2 - (text + 1)"},
		"chained":  {Expr: "calc + 1"},
		"broken":   {Expr: "nao_existe + 1"},
	}}
	if err := p.Compile(); err != nil {
		t.Fatal(err)
	}
	res, err := Evaluate(context.Background(), snmp.NewMemSource(pdus), p, "")
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]int64{"fallback": 47, "text": 42, "calc": 51, "chained": 52}
	for k, v := range want {
		if res.Counters[k] != v {
			t.Errorf("%s = %d, want %d", k, res.Counters[k], v)
		}
	}
	for _, k := range []string{"partial", "holder", "broken"} {
		if _, ok := res.Counters[k]; ok {
			t.Errorf("%s não deveria resolver", k)
		}
	}
}

func TestSelectionRules(t *testing.T) {
	profiles := loadProfiles(t)
	mk := func(id, prefix, model string) *Profile {
		return &Profile{ID: id, Version: 1, Match: &Match{SysObjectIDPrefix: prefix, ModelRegex: model}}
	}
	extra := []*Profile{
		mk("canon-c5540", "1.3.6.1.4.1.1602", "(?i)C5540"),
		mk("canon-deep", "1.3.6.1.4.1.1602.4", ""),
		mk("placeholder", Placeholder, ""),
	}
	all := append(append([]*Profile{}, profiles...), extra...)
	cases := []struct{ oid, model, want string }{
		{"1.3.6.1.4.1.1602.4.7", "iR-ADV C5540", "canon-deep"},
		{"1.3.6.1.4.1.1602.9", "iR-ADV C5540", "canon-c5540"},
		{"1.3.6.1.4.1.1602.9", "iR 1643i", "canon"},
		{"1.3.6.1.4.1.18334.1", "bizhub", "konica-minolta"},
		{"1.3.6.1.4.1.11.2", "HP", "hp"},
		{"1.3.6.1.4.1.8072.3.2.10", "Net-SNMP", "generic"}, // fabricante sem perfil
		{"1.3.6.1.4.1.16020", "x", "generic"},              // prefixo por componente, não por texto
	}
	for _, c := range cases {
		if got := Select(all, c.oid, c.model); got == nil || got.ID != c.want {
			t.Errorf("%s/%s: got %v want %s", c.oid, c.model, got, c.want)
		}
	}
	if Select(nil, "1.2", "") != nil {
		t.Error("sem perfis deveria retornar nil")
	}
}

func TestSchemaCopyIsInSync(t *testing.T) {
	orig, err := os.ReadFile(filepath.Join(profilesDir, "profile.schema.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(orig, schemaJSON) {
		t.Fatal("profile.schema.json embutido está desatualizado: rode go generate ./... em agent/")
	}
}

func TestValidationRejectsBadProfiles(t *testing.T) {
	bad := map[string]string{
		"sem id":          `{"version":1}`,
		"oid inválido":    `{"id":"x","version":1,"counters":{"total":{"oid":"abc"}}}`,
		"dois tipos":      `{"id":"x","version":1,"counters":{"total":{"oid":"1.2","expr":"a"}}}`,
		"campo estranho":  `{"id":"x","version":1,"foo":1}`,
		"regex quebrada":  `{"id":"x","version":1,"match":{"model_regex":"("}}`,
		"expr inválida":   `{"id":"x","version":1,"counters":{"t":{"expr":"a +"}}}`,
		"nome sem tabela": `{"id":"x","version":1,"counters":{"t":{"name_regex":"a"}}}`,
		"não é json":      `{`,
	}
	for name, raw := range bad {
		if _, err := FromJSON([]byte(raw)); err == nil {
			t.Errorf("%s: esperava erro", name)
		}
	}
	if _, err := FromYAML([]byte("id: [")); err == nil {
		t.Error("YAML inválido deveria falhar")
	}
	if _, err := LoadDir(t.TempDir()); err != nil {
		t.Error(err)
	}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "x.yaml"), []byte("id: X\nversion: 1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadDir(dir); err == nil {
		t.Error("id com maiúscula deveria falhar no schema")
	}
}

func TestExpressions(t *testing.T) {
	vars := map[string]int64{"a": 10, "b": 3}
	cases := map[string]int64{"a + b": 13, "a - b * 2": 4, "(a - b) * 2": 14, "-a + 1": -9, "a": 10, "7": 7}
	for e, want := range cases {
		n, err := parseExpr(e)
		if err != nil {
			t.Fatalf("%s: %v", e, err)
		}
		got, err := n.eval(vars)
		if err != nil || got != want {
			t.Errorf("%s = %d (%v), want %d", e, got, err, want)
		}
	}
	for _, e := range []string{"", "a +", "(a", "a b", "a / b", "a ) "} {
		if _, err := parseExpr(e); err == nil {
			t.Errorf("%q deveria falhar", e)
		}
	}
	big := map[string]int64{"x": 1 << 62}
	for _, e := range []string{"x * 4", "x + x + x", "0 - x - x - x"} {
		n, _ := parseExpr(e)
		if _, err := n.eval(big); err == nil {
			t.Errorf("%s deveria estourar", e)
		}
	}
	refs := map[string]bool{}
	n, _ := parseExpr("a + (b * c)")
	n.refs(refs)
	if len(refs) != 3 {
		t.Errorf("refs: %v", refs)
	}
}

func TestProfileHelpers(t *testing.T) {
	falseV := false
	p := &Profile{ID: "h", Version: 1, Supplies: &Supplies{UseStandard: &falseV}}
	if p.UseStandardSupplies() || p.SumTolerancePercent() != 2 || p.Sources() != nil {
		t.Fatal("padrões inesperados")
	}
	if _, err := p.MatchString("(", "x"); err == nil {
		t.Fatal("regex inválida deveria falhar")
	}
}

func TestCounterLinesFromProfile(t *testing.T) {
	src := snmp.NewMemSource([]snmp.PDU{
		{OID: "1.2.1", Kind: snmp.KindCounter32, Int: 100},
		{OID: "1.2.2", Kind: snmp.KindCounter32, Int: 7},
	})
	p := &Profile{ID: "t", Version: 1, Counters: map[string]Counter{
		"total":      {OID: "1.2.1"},
		"a3_color":   {OID: "1.2.2", Line: &Line{Kind: "print", ColorMode: "full_color", Size: "a3"}},
		"not_read":   {OID: "1.2.9", Line: &Line{Kind: "copy", ColorMode: "mono", Size: "a4"}},
		"no_mapping": {OID: "1.2.2"},
	}}
	if err := p.Compile(); err != nil {
		t.Fatal(err)
	}
	res, err := Evaluate(context.Background(), src, p, "")
	if err != nil {
		t.Fatal(err)
	}
	// Só contadores lidos e com `line` explícito; os nomes normalizados usam o padrão do servidor.
	if len(res.Lines) != 1 || res.Lines["a3_color"] != (Line{Kind: "print", ColorMode: "full_color", Size: "a3"}) {
		t.Fatalf("linhas: %+v", res.Lines)
	}
	raw := []byte(`{"id":"x","version":1,"counters":{"t":{"oid":"1.2.1","line":{"kind":"print","color_mode":"mono","size":"a5"}}}}`)
	if _, err := FromJSON(raw); err == nil || !strings.Contains(err.Error(), "size") {
		t.Fatalf("tamanho inválido deveria ser recusado pelo schema: %v", err)
	}
	raw = []byte(`{"id":"x","version":1,"counters":{"t":{"oid":"1.2.1"}},` +
		`"supplies":{"cartridge_serial_oid":"1.3.6.1.4.1.9.9"},` +
		`"attributes":{"ssid":[{"oid":"1.2.3"}],"parts":{"drum_k":{"oid":"1.2.4","part":"drum","unit":"percent","color":"black"}}}}`)
	if _, err := FromJSON(raw); err != nil {
		t.Fatalf("atributos do perfil: %v", err)
	}
}

// TestBaseVendorProfiles: the base profiles of the other vendors (PROMPT 6.4) match their enterprise id and,
// while the proprietary OIDs are still PREENCHER_PELO_WALK, read the total from the standard source.
func TestBaseVendorProfiles(t *testing.T) {
	profiles := loadProfiles(t)
	vendors := map[string]string{
		"hp": "11", "ricoh": "367", "kyocera": "1347", "xerox": "253", "brother": "2435", "samsung": "236",
		"lexmark": "641", "sharp": "2385", "epson": "1248", "oki": "2001", "toshiba": "1129",
	}
	src := snmp.NewMemSource([]snmp.PDU{
		{OID: "1.3.6.1.2.1.43.10.2.1.4.1.1", Kind: snmp.KindCounter32, Int: 4321},
	})
	for id, ent := range vendors {
		p := Select(profiles, "1.3.6.1.4.1."+ent+".1.1", "Modelo qualquer")
		if p == nil || p.ID != id {
			t.Fatalf("enterprise %s: perfil %v, esperado %s", ent, p, id)
		}
		res, err := Evaluate(context.Background(), src, p, "Modelo qualquer")
		if err != nil {
			t.Fatal(err)
		}
		if res.Source != "standard" || res.Counters["total"] != 4321 {
			t.Fatalf("%s: fonte %q total %d", id, res.Source, res.Counters["total"])
		}
	}
}
