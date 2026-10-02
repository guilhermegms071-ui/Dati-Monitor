package collector

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

func TestScanNowRespectsRoleRangeAndRemovalRules(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	if _, err := f.c.ScanNow(ctx, ""); !errors.Is(err, ErrNoConfig) {
		t.Fatalf("sem configuração: %v", err)
	}
	f.net.set("10.0.0.1:161", "03-konica-cor")
	f.net.set("10.0.1.1:161", "01-canon-cor")
	cfg := f.config(t,
		protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32", Ports: []int{161}},
		protocol.IPRange{ID: "r2", CIDR: "10.0.1.1/32", Ports: []int{161}},
	)
	cfg.ClusterRole = "standby"
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := f.c.ScanNow(ctx, ""); !errors.Is(err, ErrNotMaster) {
		t.Fatalf("standby: %v", err)
	}
	f.c.SetRole("master", true)
	if _, err := f.c.ScanNow(ctx, ""); !errors.Is(err, ErrPaused) {
		t.Fatalf("pausado: %v", err)
	}
	f.c.SetRole("master", false)
	if _, err := f.c.ScanNow(ctx, "inexistente"); err == nil || !strings.Contains(err.Error(), "não está na configuração") {
		t.Fatalf("faixa inexistente: %v", err)
	}

	res, err := f.c.ScanNow(ctx, "r1")
	if err != nil || res.Ranges != 1 || res.Found != 1 || res.New != 1 || res.Targets != 1 {
		t.Fatalf("só a faixa r1: %+v %v", res, err)
	}
	res, err = f.c.ScanNow(ctx, "")
	if err != nil || res.Ranges != 2 || res.Found != 2 || res.New != 1 || res.Removed != 0 {
		t.Fatalf("todas as faixas: %+v %v", res, err)
	}
	if f.c.LastScan().IsZero() {
		t.Fatal("varredura completa atualiza LastScan")
	}
	// Faixa parcial nunca remove equipamentos das outras faixas.
	if _, err := f.c.ScanNow(ctx, "r2"); err != nil {
		t.Fatal(err)
	}
	if n := f.c.KnownDevices(ctx); n != 2 {
		t.Fatalf("equipamentos: %d", n)
	}
	// Local sem faixa aprovada.
	empty := f.config(t)
	if err := f.c.Apply(empty); err != nil {
		t.Fatal(err)
	}
	if _, err := f.c.ScanNow(ctx, ""); err == nil || !strings.Contains(err.Error(), "sem faixa") && !strings.Contains(err.Error(), "não tem faixa") {
		t.Fatalf("sem faixa: %v", err)
	}
}

func TestReadNowSelectedAndAll(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.net.set("10.0.0.1:161", "03-konica-cor")
	f.net.set("10.0.0.2:161", "02-canon-pb")
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", Start: "10.0.0.1", End: "10.0.0.2", Ports: []int{161}})); err != nil {
		t.Fatal(err)
	}
	if _, err := f.c.ScanNow(ctx, ""); err != nil {
		t.Fatal(err)
	}
	sum, err := f.c.ReadNow(ctx, []Selector{{Serial: "A797019500624"}, {IP: "10.9.9.9", Port: 161}})
	if err != nil {
		t.Fatal(err)
	}
	if sum.Requested != 2 || sum.OK != 1 || sum.Failed != 1 || len(sum.NotFound) != 1 || !sum.Devices[0].OK {
		t.Fatalf("seleção: %+v", sum)
	}
	if n := len(filter(f.items(t), protocol.KindReading, "A797019500624")); n != 1 {
		t.Fatalf("leitura sob demanda vai para a fila: %d", n)
	}
	// Uma impressora deixa de responder: a leitura sob demanda informa a falha.
	f.net.off("10.0.0.2:161")
	sum, err = f.c.ReadNow(ctx, nil)
	if err != nil || sum.Requested != 2 || sum.OK != 1 || sum.Failed != 1 {
		t.Fatalf("todos: %+v %v", sum, err)
	}
	for _, d := range sum.Devices {
		if d.IP == "10.0.0.2" && (d.OK || d.Error == "") {
			t.Fatalf("falha deveria vir com o erro: %+v", d)
		}
	}
}

func TestReadRawSNMPTestAndWalk(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	cfg := f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32"})
	// Primeira credencial errada, segunda certa: connectAny tenta em ordem.
	cfg.Credentials = []snmp.Credential{{ID: "errada", Version: "v2c", Community: "xyz"}, {ID: "public", Version: "v2c", Community: "public"}}
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	f.net.set("10.0.0.1:161", "03-konica-cor")
	raw, err := f.c.ReadRaw(ctx, "10.0.0.1", 161, nil)
	if err != nil {
		t.Fatal(err)
	}
	counters, ok := raw["counters"].(map[string]int64)
	if !ok || counters["total"] != 217031 || raw["profile"] != "konica-minolta" || raw["credential_id"] != "public" {
		t.Fatalf("leitura bruta: %+v", raw)
	}
	if raw["status"] == nil || raw["supplies"] == nil {
		t.Fatalf("status/suprimentos: %+v", raw)
	}
	// Rascunho de perfil (tela Perfis de modelos): substitui o perfil escolhido só nesta leitura.
	draft, err := profile.FromJSON([]byte(`{"id":"rascunho","version":1,"counters":{` +
		`"total":{"oid":"1.3.6.1.4.1.18334.1.1.1.5.7.2.1.1.0"},"duplex":{"oid":"1.3.6.1.4.1.18334.1.1.1.5.7.2.1.3.0"}}}`))
	if err != nil {
		t.Fatal(err)
	}
	tested, err := f.c.ReadRaw(ctx, "10.0.0.1", 161, draft)
	if err != nil {
		t.Fatal(err)
	}
	tc, _ := tested["counters"].(map[string]int64)
	if tested["profile"] != "rascunho" || tested["profile_draft"] != true || tc["total"] != 217031 || tc["duplex"] != 5000 {
		t.Fatalf("leitura com rascunho: %+v", tested)
	}
	if n := len(f.items(t)); n != 0 {
		t.Fatalf("leitura bruta não entra na fila: %d itens", n)
	}

	tests, err := f.c.SNMPTest(ctx, "10.0.0.1", 161)
	if err != nil || len(tests) != 2 {
		t.Fatalf("%+v %v", tests, err)
	}
	if tests[0].OK || tests[0].Error == "" || !tests[1].OK || !tests[1].IsPrinter || tests[1].Serial != "A797019500624" || tests[1].SysDescr == "" {
		t.Fatalf("teste de credenciais: %+v", tests)
	}

	pdus, credID, err := f.c.Walk(ctx, "10.0.0.1", 161, "1.3.6.1.2.1.43", nil)
	if err != nil || credID != "public" || len(pdus) == 0 {
		t.Fatalf("walk: %d %s %v", len(pdus), credID, err)
	}
	for _, p := range pdus {
		if !snmp.HasPrefix(p.OID, "1.3.6.1.2.1.43") {
			t.Fatalf("OID fora da subárvore: %s", p.OID)
		}
	}
	all, _, err := f.c.Walk(ctx, "10.0.0.1", 161, "", func(int) {})
	if err != nil || len(all) <= len(pdus) {
		t.Fatalf("walk completo: %d %v", len(all), err)
	}
	if _, _, err := f.c.Walk(ctx, "10.0.0.1", 161, "1.3.6.1.9.9.9", nil); err == nil {
		t.Fatal("subárvore vazia deveria dar erro claro")
	}
	if _, err := f.c.ReadRaw(ctx, "10.0.0.99", 161, nil); err == nil || !strings.Contains(err.Error(), "nenhuma credencial") {
		t.Fatalf("sem resposta: %v", err)
	}
	cfg.Credentials = nil
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := f.c.SNMPTest(ctx, "10.0.0.1", 161); err == nil {
		t.Fatal("sem credenciais")
	}
	if _, err := f.c.ReadRaw(ctx, "10.0.0.1", 161, nil); err == nil {
		t.Fatal("sem credenciais")
	}
}
