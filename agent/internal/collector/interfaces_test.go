package collector

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

// setReal serves a walk of a real printer (profiles/recordings/real, Fase 10).
func (n *simNet) setReal(addr, recording string) {
	n.t.Helper()
	f, err := os.Open(filepath.Join(profilesDir, "recordings", "real", recording+".snmprec"))
	if err != nil {
		n.t.Fatal(err)
	}
	defer func() { _ = f.Close() }()
	pdus, err := snmp.ParseSnmprec(f)
	if err != nil {
		n.t.Fatal(err)
	}
	n.mu.Lock()
	n.hosts[addr] = snmp.NewMemSource(pdus)
	n.mu.Unlock()
}

func TestIsPrintController(t *testing.T) {
	for descr, want := range map[string]bool{
		"KONICA MINOLTA AccurioPrint C4065 IC-607": true,
		"EFI Fiery Color Server":                   true,
		"Xerox EX-i C60-C70 Print Server":          true,
		"Creo Color Server":                        true,
		"KONICA MINOLTA AccurioPrint C4065":        false,
		"KONICA MINOLTA bizhub C287":               false,
		"Canon iR-ADV C5540":                       false,
		"HP ETHERNET MULTI-ENVIRONMENT, ROM J.sp.00, JETDIRECT EX and JD28 EEPROM 5.58": false,
	} {
		if got := isPrintController(printer.Identity{SysDescr: descr}); got != want {
			t.Errorf("%q: controladora=%v, esperado %v", descr, got, want)
		}
	}
}

// Rede real da Fase 10: a AccurioPrint C4065 responde em .190 (a própria impressora) e em .199 (a Fiery
// IC-607) com o mesmo serial. A Fiery conta só o que passa por ela (sem cópias, sem PB/cor): o
// equipamento é registrado uma vez, pela impressora, e a controladora sai da lista de leitura.
func TestSameSerialKeepsPrinterInterfaceNotController(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	f.net.setReal("10.10.10.190:161", "konica_accurioprint_c4065")
	f.net.setReal("10.10.10.199:161", "konica_accurioprint_c4065_fiery_ic607")
	f.net.setReal("10.10.10.198:161", "konica_bizhub_c287")
	// Estado antigo (antes da regra): a Fiery estava registrada e alternava o IP com a impressora.
	if err := f.st.UpsertDevice(ctx, store.Device{IP: "10.10.10.199", Port: 161, Serial: "ACC2011022817"}); err != nil {
		t.Fatal(err)
	}
	rng := protocol.IPRange{ID: "r1", CIDR: "10.10.10.0/24", Ports: []int{161}}
	if err := f.c.Apply(f.config(t, rng)); err != nil {
		t.Fatal(err)
	}
	res, err := f.c.scanRanges(ctx, []protocol.IPRange{rng}, true)
	if err != nil {
		t.Fatal(err)
	}
	if res.Found != 2 {
		t.Fatalf("equipamentos registrados = %d, esperado 2 (C4065 uma vez + C287): %+v", res.Found, res)
	}
	devs, err := f.st.Devices(ctx)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]string{}
	for _, d := range devs {
		got[d.IP] = d.Serial
	}
	if got["10.10.10.190"] != "ACC2011022817" || got["10.10.10.198"] != "A797017500038" || len(got) != 2 {
		t.Fatalf("lista de leitura = %v; esperado só .190 (impressora) e .198", got)
	}

	// Com a impressora desligada, a Fiery sozinha continua fora: o serial já é lido pela .190.
	f.net.off("10.10.10.190:161")
	if _, err := f.c.scanRanges(ctx, []protocol.IPRange{rng}, true); err != nil {
		t.Fatal(err)
	}
	if d, err := f.st.DeviceAt(ctx, "10.10.10.199", 161); err != nil || d != nil {
		t.Fatalf("a controladora voltou para a lista de leitura: %+v (%v)", d, err)
	}
	if d, err := f.st.DeviceAt(ctx, "10.10.10.190", 161); err != nil || d == nil {
		t.Fatalf("a impressora saiu da lista por estar desligada numa varredura: %v", err)
	}
}

func TestSplitInterfacesPrefersLowestIPBetweenEquals(t *testing.T) {
	mk := func(ip string) candidate {
		c := candidate{id: printer.Identity{Serial: "S1", SysDescr: "Canon iR-ADV C5540"}}
		c.found.Target.IP, c.found.Target.Port = ip, 161
		return c
	}
	p, s := splitInterfaces([]candidate{mk("10.0.0.20"), mk("10.0.0.3")}, nil)
	if len(p) != 1 || p[0].found.Target.IP != "10.0.0.3" || len(s) != 1 || s[0].primary != "10.0.0.3:161" {
		t.Fatalf("principal %+v, secundárias %+v", p, s)
	}
}

// A leitura abre a conexão com o ajuste do bloco "snmp" do perfil para o modelo do equipamento (ECOSYS
// M3655idn: link com perda na rede real); os outros modelos seguem com as opções do coletor.
func TestReadUsesProfileSNMPTuningForTheModel(t *testing.T) {
	f := newFixture(t)
	ctx := context.Background()
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.10.10.0/24", Ports: []int{161}})); err != nil {
		t.Fatal(err)
	}
	f.net.set("10.10.10.147:161", "05-generica")
	f.net.set("10.10.10.191:161", "05-generica")
	for ip, model := range map[string]string{"10.10.10.147": "ECOSYS M3655idn", "10.10.10.191": "ECOSYS M3550idn"} {
		dev := store.Device{IP: ip, Port: 161, Serial: "S" + ip, Model: model, ProfileKey: "kyocera", CredentialID: "public"}
		if err := f.st.UpsertDevice(ctx, dev); err != nil {
			t.Fatal(err)
		}
		_ = f.c.readDevice(ctx, dev, []string{TaskStatus})
	}
	f.net.mu.Lock()
	defer f.net.mu.Unlock()
	tuned, plain := f.net.opts["10.10.10.147:161"], f.net.opts["10.10.10.191:161"]
	if tuned.Retries != 6 || tuned.Timeout != time.Second {
		t.Fatalf("M3655idn deveria ler com 6 tentativas de 1 s: %+v", tuned)
	}
	if plain.Retries == 6 {
		t.Fatalf("M3550idn não deveria receber o ajuste da M3655idn: %+v", plain)
	}
}
