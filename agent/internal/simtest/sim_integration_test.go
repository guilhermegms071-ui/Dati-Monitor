//go:build integration

package simtest

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/discovery"
	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

func profiles(t *testing.T) []*profile.Profile {
	t.Helper()
	ps, err := profile.LoadDir(filepath.Join(RepoRoot(t), "profiles"))
	if err != nil {
		t.Fatal(err)
	}
	return ps
}

func dial(t *testing.T, port int, cred snmp.Credential) *snmp.Client {
	t.Helper()
	c, err := snmp.Dial("127.0.0.1", port, cred, snmp.DefaultOptions())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Close() })
	return c
}

// As leituras pelo SNMP real (UDP + GETBULK) batem exatamente com as gravações (critério 12).
func TestProfilesOverRealSNMP(t *testing.T) {
	ps := profiles(t)
	cases := []struct {
		sim, profile string
		want         map[string]int64
	}{
		{"01-canon-cor", "canon", map[string]int64{"total": 150000, "mono": 90000, "color": 60000, "scan": 33333}},
		{"02-canon-pb", "canon", map[string]int64{"total": 45678, "mono": 45678, "color": 0}},
		{"03-konica-cor", "konica-minolta", map[string]int64{"total": 217031, "mono": 100150, "color": 116881}},
		{"04-konica-pb", "konica-minolta", map[string]int64{"total": 88000, "mono": 88000, "color": 0}},
		{"05-generica", "generic", map[string]int64{"total": 48213}},
	}
	for _, tc := range cases {
		t.Run(tc.sim, func(t *testing.T) {
			ctx := context.Background()
			c := dial(t, Start(t, tc.sim), Public)
			id, p, err := printer.ReadIdentity(ctx, c, ps)
			if err != nil {
				t.Fatal(err)
			}
			if p.ID != tc.profile {
				t.Fatalf("perfil %s, esperado %s", p.ID, tc.profile)
			}
			res, err := profile.Evaluate(ctx, c, p, id.Model)
			if err != nil {
				t.Fatal(err)
			}
			for k, v := range tc.want {
				if res.Counters[k] != v {
					t.Errorf("%s = %d, esperado %d", k, res.Counters[k], v)
				}
			}
			if tc.sim == "03-konica-cor" && res.Counters["mono"]+res.Counters["color"] != res.Counters["total"] {
				t.Error("Konica: total deveria ser PB + cor")
			}
			sup, err := printer.ReadSupplies(ctx, c)
			if err != nil || len(sup) == 0 {
				t.Fatalf("suprimentos: %v %v", sup, err)
			}
		})
	}
}

func TestStatusAndErrorsOverRealSNMP(t *testing.T) {
	c := dial(t, Start(t, "07-erros"), Public)
	st, err := printer.ReadStatus(context.Background(), c, nil)
	if err != nil {
		t.Fatal(err)
	}
	if st.Status != printer.StatusError || len(st.Reasons) != 2 || len(st.Alerts) != 2 {
		t.Fatalf("%+v", st)
	}
}

// Critério 13: impressora em economia de energia só responde na 2ª tentativa e NÃO fica sem resposta.
func TestEnergySavingPrinterAnswersOnSecondAttempt(t *testing.T) {
	const idle = 2 * time.Second
	port := StartSleepy(t, "06-economia", idle, time.Second)
	ctx := context.Background()
	c := dial(t, port, Public) // timeout 1,5 s e 1 retentativa (padrão do coletor)
	start := time.Now()
	v, err := c.Get(ctx, []string{"1.3.6.1.2.1.43.5.1.1.17.1"})
	if err != nil {
		t.Fatalf("deveria responder na 2ª tentativa: %v", err)
	}
	if v[0].String() != "SIMSLEEP06" || time.Since(start) < time.Second {
		t.Fatalf("valor=%q em %v (a 1ª tentativa deveria ter sido descartada)", v[0].String(), time.Since(start))
	}
	st, err := printer.ReadStatus(ctx, c, profile.Select(profiles(t), "1.3.6.1.4.1.8072.3.2.10", ""))
	if err != nil || st.Status != printer.StatusEnergySaving {
		t.Fatalf("status %+v %v", st, err)
	}
	// Sem retentativa (como o sistema anterior, 0,5 s sem retry) ela pareceria desligada.
	time.Sleep(idle + 500*time.Millisecond) // volta a dormir
	noRetry, err := snmp.Dial("127.0.0.1", port, Public, snmp.Options{Timeout: 500 * time.Millisecond, Retries: 0})
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = noRetry.Close() }()
	if _, err := noRetry.Get(ctx, []string{"1.3.6.1.2.1.1.2.0"}); !errors.Is(err, snmp.ErrTimeout) {
		t.Fatalf("sem retentativa deveria dar timeout, veio %v", err)
	}
}

func TestSNMPv3AuthPriv(t *testing.T) {
	c := dial(t, StartV3(t, "05-generica"), V3)
	v, err := c.Get(context.Background(), []string{"1.3.6.1.2.1.43.5.1.1.17.1"})
	if err != nil || v[0].String() != "SIMGEN0005" {
		t.Fatalf("%v %v", v, err)
	}
	wrong := V3
	wrong.V3AuthPass = "senha-errada-1"
	bad, err := snmp.Dial("127.0.0.1", StartV3(t, "05-generica"), wrong, snmp.Options{Timeout: 700 * time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = bad.Close() }()
	if _, err := bad.Get(context.Background(), []string{"1.3.6.1.2.1.1.2.0"}); err == nil {
		t.Fatal("senha errada deveria falhar")
	}
}

func TestDiscoveryFindsSimulatedPrintersOnDifferentPorts(t *testing.T) {
	ports := []int{Start(t, "01-canon-cor"), Start(t, "03-konica-cor"), Start(t, "05-generica")}
	targets, err := discovery.Expand([]protocol.IPRange{{CIDR: "127.0.0.1/32", Ports: append(ports, FreeUDPPort(t))}})
	if err != nil {
		t.Fatal(err)
	}
	sc := &discovery.Scanner{
		Credentials: []snmp.Credential{{ID: "errada", Version: "v2c", Community: "nada"}, Public},
		Options:     snmp.Options{Timeout: 700 * time.Millisecond, Retries: 0}, Concurrency: 8, RatePPS: 200,
	}
	var found []discovery.Found
	if _, err := sc.Scan(context.Background(), targets, func(f discovery.Found) { found = append(found, f) }); err != nil {
		t.Fatal(err)
	}
	if len(found) != 3 {
		t.Fatalf("encontradas %d impressoras: %+v", len(found), found)
	}
	for _, f := range found {
		if f.CredentialID != "public" || !f.Probe.IsPrinter() {
			t.Errorf("%+v", f)
		}
	}
}

// Cenário 8 da seção 13: o contador regride entre duas leituras (troca do arquivo snmprec no meio).
func TestCounterRegressionScenario(t *testing.T) {
	ctx := context.Background()
	src := Recording(t, "08-regressao")
	dir := t.TempDir()
	copyFile := func(from string) {
		raw, err := os.ReadFile(filepath.Join(src, from)) //nolint:gosec // G304: gravação do repositório
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "public.snmprec"), raw, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	read := func(port int) int64 {
		c := dial(t, port, Public)
		v, err := c.Get(ctx, []string{"1.3.6.1.2.1.43.10.2.1.4.1.1"})
		if err != nil {
			t.Fatal(err)
		}
		n, _ := v[0].Number()
		return n
	}
	port := FreeUDPPort(t)
	copyFile("public.snmprec")
	cmd := StartDir(t, dir, port)
	if got := read(port); got != 500000 {
		t.Fatalf("1ª leitura %d", got)
	}
	_ = cmd.Process.Kill()
	_, _ = cmd.Process.Wait()
	copyFile("regressed.snmprec.txt")
	StartDir(t, dir, port)
	if got := read(port); got != 400000 {
		t.Fatalf("2ª leitura %d (deveria ter regredido)", got)
	}
}

// Walk real gravado em .snmprec reproduz as mesmas leituras (base do dm-tool walk / Fase 10).
func TestWalkRoundTrip(t *testing.T) {
	ctx := context.Background()
	c := dial(t, Start(t, "03-konica-cor"), Public)
	var pdus []snmp.PDU
	if err := c.Walk(ctx, "1.3.6.1", func(p snmp.PDU) error { pdus = append(pdus, p); return nil }); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(t.TempDir(), "walk.snmprec")
	f, err := os.Create(out) //nolint:gosec // G304: arquivo temporário
	if err != nil {
		t.Fatal(err)
	}
	if err := snmp.WriteSnmprec(f, pdus); err != nil {
		t.Fatal(err)
	}
	_ = f.Close()
	rf, err := os.Open(out) //nolint:gosec // G304: idem
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = rf.Close() }()
	again, err := snmp.ParseSnmprec(rf)
	if err != nil {
		t.Fatal(err)
	}
	mem := snmp.NewMemSource(again)
	p := profile.Select(profiles(t), "1.3.6.1.4.1.18334.1", "bizhub C287")
	res, err := profile.Evaluate(ctx, mem, p, "bizhub C287")
	if err != nil || res.Counters["total"] != 217031 || res.Counters["mono"] != 100150 {
		t.Fatalf("%+v %v", res, err)
	}
}
