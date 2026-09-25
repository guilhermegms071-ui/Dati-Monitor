package collector

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"sync"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/discovery"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

const profilesDir = "../../../profiles"

// simNet serves the simulator recordings (profiles/recordings/sim) from memory, keyed by "ip:port".
// Addresses without a recording behave like a host that never answers.
type simNet struct {
	t     *testing.T
	mu    sync.Mutex
	hosts map[string]*snmp.MemSource
	dials int
}

type simConn struct{ src *snmp.MemSource }

func (c simConn) Get(ctx context.Context, oids []string) ([]snmp.PDU, error) {
	if c.src == nil {
		return nil, snmp.ErrTimeout
	}
	return c.src.Get(ctx, oids)
}

func (c simConn) Walk(ctx context.Context, root string, fn func(snmp.PDU) error) error {
	if c.src == nil {
		return snmp.ErrTimeout
	}
	return c.src.Walk(ctx, root, fn)
}

func (simConn) Close() error { return nil }

func newSimNet(t *testing.T) *simNet { return &simNet{t: t, hosts: map[string]*snmp.MemSource{}} }

func (n *simNet) set(addr, recording string) {
	n.t.Helper()
	f, err := os.Open(filepath.Join(profilesDir, "recordings", "sim", recording, "public.snmprec"))
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

func (n *simNet) off(addr string) {
	n.mu.Lock()
	delete(n.hosts, addr)
	n.mu.Unlock()
}

func (n *simNet) dial(host string, port int, cred snmp.Credential, _ snmp.Options) (discovery.Conn, error) {
	n.mu.Lock()
	defer n.mu.Unlock()
	n.dials++
	if cred.Community != "public" {
		return simConn{}, nil
	}
	return simConn{n.hosts[key(host, port)]}, nil
}

func loadProfiles(t *testing.T) []json.RawMessage {
	t.Helper()
	ps, err := profile.LoadDir(profilesDir)
	if err != nil {
		t.Fatal(err)
	}
	var out []json.RawMessage
	for _, p := range ps {
		raw, err := json.Marshal(p)
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, raw)
	}
	return out
}

type fixture struct {
	c     *Collector
	st    *store.Store
	net   *simNet
	clock time.Time
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	f := &fixture{st: st, net: newSimNet(t), clock: time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC)}
	f.c = New(Deps{
		Store: st, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Health: health.NewRegistry(),
		Dial: f.net.dial, Clock: func() time.Time { return f.clock },
	})
	return f
}

func (f *fixture) config(t *testing.T, ranges ...protocol.IPRange) *protocol.AgentConfig {
	t.Helper()
	return &protocol.AgentConfig{
		V: protocol.Version, ConfigVersion: 1, ClusterRole: "master", Ranges: ranges,
		Credentials: []snmp.Credential{{ID: "public", Version: "v2c", Community: "public"}},
		Profiles:    loadProfiles(t),
		Discovery:   protocol.DiscoveryConfig{Concurrency: 8, RatePPS: 10000, TimeoutMS: 100},
	}
}

// items returns every pending outbox item decoded.
func (f *fixture) items(t *testing.T) []protocol.Item {
	t.Helper()
	pending, err := f.st.Pending(context.Background(), 10000)
	if err != nil {
		t.Fatal(err)
	}
	out := make([]protocol.Item, 0, len(pending))
	for _, p := range pending {
		var it protocol.Item
		if err := json.Unmarshal(p.Payload, &it); err != nil {
			t.Fatal(err)
		}
		if it.Kind != p.Kind {
			t.Fatalf("tipo divergente: %s vs %s", it.Kind, p.Kind)
		}
		out = append(out, it)
	}
	return out
}

func filter(items []protocol.Item, kind, serial string) []protocol.Item {
	var out []protocol.Item
	for _, it := range items {
		if it.Kind == kind && (serial == "" || it.Device.Serial == serial) {
			out = append(out, it)
		}
	}
	return out
}

func TestScanRegistersPrintersAndReadsEveryTask(t *testing.T) {
	f := newFixture(t)
	f.net.set("10.0.0.1:161", "03-konica-cor")
	f.net.set("10.0.0.2:161", "01-canon-cor")
	// 10.0.0.3 não responde. Um equipamento antigo fora das faixas deve sair da lista.
	if err := f.st.UpsertDevice(context.Background(), store.Device{IP: "10.9.9.9", Port: 161, Serial: "VELHO"}); err != nil {
		t.Fatal(err)
	}
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", Start: "10.0.0.1", End: "10.0.0.3", Ports: []int{161}})); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { f.c.Run(ctx); close(done) }()
	deadline := time.Now().Add(20 * time.Second)
	for {
		items := f.items(t)
		if len(filter(items, protocol.KindReading, "")) >= 2 && len(filter(items, protocol.KindStatus, "")) >= 2 &&
			len(filter(items, protocol.KindSupplies, "")) >= 2 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("leituras não chegaram à fila: %d itens", len(items))
		}
		time.Sleep(50 * time.Millisecond)
	}
	cancel()
	<-done

	items := f.items(t)
	km := filter(items, protocol.KindReading, "A797019500624")
	if len(km) != 1 {
		t.Fatalf("leituras da Konica: %d", len(km))
	}
	r := km[0]
	// Caso conferido com o Datacount: total 217031 = PB 100150 + cor 116881.
	if r.Reading.Counters["total"] != 217031 || r.Device.ProfileKey != "konica-minolta" || r.Reading.ProfileKey != "konica-minolta" {
		t.Fatalf("leitura Konica: %+v / %+v", r.Reading, r.Device)
	}
	if !r.ReadAt.Equal(f.clock) || r.Device.IP != "10.0.0.1" || r.Device.Port != 161 || r.Device.MAC == "" {
		t.Fatalf("referência do equipamento: %+v em %v", r.Device, r.ReadAt)
	}
	canon := filter(items, protocol.KindReading, "SIMCAN0001")
	if len(canon) != 1 || canon[0].Reading.Counters["total"] != 150000 || canon[0].Device.ProfileKey != "canon" {
		t.Fatalf("leitura Canon: %+v", canon)
	}
	if st := filter(items, protocol.KindStatus, "SIMCAN0001"); len(st) != 1 || st[0].Status.Status == "" {
		t.Fatalf("status Canon: %+v", st)
	}
	if sup := filter(items, protocol.KindSupplies, "SIMCAN0001"); len(sup) != 1 || len(sup[0].Supplies) != 5 {
		t.Fatalf("suprimentos Canon: %+v", sup)
	}
	devs, err := f.st.Devices(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(devs) != 2 || f.c.KnownDevices(context.Background()) != 2 {
		t.Fatalf("equipamentos conhecidos: %+v", devs)
	}
	for _, d := range devs {
		if d.IP == "10.9.9.9" || d.CredentialID != "public" || d.LastOK.IsZero() {
			t.Fatalf("equipamento: %+v", d)
		}
	}
	if f.c.LastScan().IsZero() || f.c.LastRead().IsZero() {
		t.Fatal("LastScan/LastRead deveriam estar preenchidos")
	}
	if !slices.Equal(f.c.Profiles(), []string{"canon", "generic", "konica-minolta"}) {
		t.Fatalf("perfis: %v", f.c.Profiles())
	}
	if s := f.c.d.Health.Snapshot(); s.Status != "ok" {
		t.Fatalf("saúde: %+v", s)
	}
}

func TestTickDoesNothingUnlessMasterAndActive(t *testing.T) {
	f := newFixture(t)
	f.net.set("10.0.0.1:161", "01-canon-cor")
	cfg := f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32", Ports: []int{161}})
	cfg.ClusterRole = "standby"
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	f.c.tick(ctx)
	f.c.SetRole("master", true)
	f.c.tick(ctx)
	f.c.wg.Wait()
	if f.net.dials != 0 || f.c.Role() != "master" || !f.c.Paused() {
		t.Fatalf("standby/pausado não pode varrer nem ler (conexões: %d)", f.net.dials)
	}
	f.c.SetRole("master", false)
	f.c.tick(ctx) // varre
	f.c.wg.Wait()
	f.c.tick(ctx) // lê
	f.c.wg.Wait()
	if n := len(filter(f.items(t), protocol.KindReading, "SIMCAN0001")); n != 1 {
		t.Fatalf("leituras: %d", n)
	}
	// Nada vence de novo antes do intervalo; RequestRead força tudo.
	f.c.tick(ctx)
	f.c.wg.Wait()
	if n := len(filter(f.items(t), protocol.KindReading, "")); n != 1 {
		t.Fatalf("leu antes do intervalo: %d", n)
	}
	f.c.RequestRead()
	f.c.tick(ctx)
	f.c.wg.Wait()
	if n := len(filter(f.items(t), protocol.KindReading, "")); n != 2 {
		t.Fatalf("RequestRead deveria forçar a leitura: %d", n)
	}
	// RequestScan marca a varredura como devida.
	f.c.RequestScan()
	if !f.c.scanReq.Load() {
		t.Fatal("RequestScan")
	}
}

func TestReadFailureRetriesThenReportsOncePerDay(t *testing.T) {
	f := newFixture(t)
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32"})); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	dev := store.Device{IP: "10.0.0.7", Port: 161, Serial: "SIMSLEEP06", CredentialID: "public", ProfileKey: "generic"}
	if err := f.st.UpsertDevice(ctx, dev); err != nil {
		t.Fatal(err)
	}
	k := key(dev.IP, dev.Port)
	for attempt := 1; attempt < MaxAttempts; attempt++ {
		f.c.readDevice(ctx, dev, []string{TaskCounters})
		st := f.c.state[k]
		if st.attempts != attempt || time.Until(st.retryAt) < RetryDelay-time.Second {
			t.Fatalf("tentativa %d: %+v", attempt, st)
		}
		if len(f.items(t)) != 0 {
			t.Fatal("falha antes da 3ª tentativa não gera evento")
		}
	}
	f.c.readDevice(ctx, dev, []string{TaskCounters})
	ev := filter(f.items(t), protocol.KindEvent, "SIMSLEEP06")
	if len(ev) != 1 || ev[0].Event.Type != "read_failed" || ev[0].Event.Data["consecutive_failures"].(float64) != 3 {
		t.Fatalf("evento read_failed: %+v", ev)
	}
	if st := f.c.state[k]; st.attempts != 0 || !st.retryAt.IsZero() || st.next[TaskCounters].Before(time.Now().Add(59*time.Minute)) {
		t.Fatalf("depois de 3 falhas espera o próximo intervalo: %+v", st)
	}
	// Mais 3 falhas no mesmo dia: não repete o evento.
	for range MaxAttempts {
		f.c.readDevice(ctx, dev, []string{TaskCounters})
	}
	if n := len(filter(f.items(t), protocol.KindEvent, "")); n != 1 {
		t.Fatalf("read_failed repetido no mesmo dia: %d", n)
	}
	// Responde de novo (economia de energia acabou): zera as falhas.
	f.net.set(k, "06-economia")
	f.c.readDevice(ctx, dev, []string{TaskCounters})
	got, err := f.st.DeviceAt(ctx, dev.IP, dev.Port)
	if err != nil || got.Failures != 0 || got.LastOK.IsZero() {
		t.Fatalf("depois de responder: %+v %v", got, err)
	}
	if len(filter(f.items(t), protocol.KindReading, "SIMSLEEP06")) != 1 {
		t.Fatal("leitura depois de acordar")
	}
}

func TestStatusIsSentOnlyOnChange(t *testing.T) {
	f := newFixture(t)
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32"})); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	f.net.set("10.0.0.5:161", "01-canon-cor")
	dev := store.Device{IP: "10.0.0.5", Port: 161, Serial: "SIMCAN0001", CredentialID: "public", ProfileKey: "canon"}
	if err := f.st.UpsertDevice(ctx, dev); err != nil {
		t.Fatal(err)
	}
	read := func() {
		d, err := f.st.DeviceAt(ctx, dev.IP, dev.Port)
		if err != nil {
			t.Fatal(err)
		}
		f.c.readDevice(ctx, *d, []string{TaskStatus})
	}
	read()
	read()
	if n := len(filter(f.items(t), protocol.KindStatus, "")); n != 1 {
		t.Fatalf("status igual não deve ser reenviado: %d", n)
	}
	f.net.set("10.0.0.5:161", "07-erros") // atolamento + porta aberta (0x0c00)
	read()
	st := filter(f.items(t), protocol.KindStatus, "")
	if len(st) != 2 || st[1].Status.Status == st[0].Status.Status || st[1].Status.ErrorBits == 0 {
		t.Fatalf("mudança de status: %+v", st)
	}
}

func TestSerialChangeOnSameIPRefreshesIdentity(t *testing.T) {
	f := newFixture(t)
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32"})); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	f.net.set("10.0.0.1:161", "03-konica-cor")
	if _, err := f.c.register(ctx, discovery.Found{Target: discovery.Target{IP: "10.0.0.1", Port: 161}, CredentialID: "public"}); err != nil {
		t.Fatal(err)
	}
	// A Konica foi trocada por uma Canon no mesmo IP.
	f.net.set("10.0.0.1:161", "02-canon-pb")
	d, err := f.st.DeviceAt(ctx, "10.0.0.1", 161)
	if err != nil {
		t.Fatal(err)
	}
	f.c.readDevice(ctx, *d, []string{TaskCounters})
	r := filter(f.items(t), protocol.KindReading, "")
	if len(r) != 1 || r[0].Device.Serial != "SIMCAN0002" || r[0].Reading.ProfileKey != "canon" || r[0].Reading.Counters["total"] != 45678 {
		t.Fatalf("leitura depois da troca: %+v", r)
	}
	if d, _ := f.st.DeviceAt(ctx, "10.0.0.1", 161); d.Serial != "SIMCAN0002" || d.ProfileKey != "canon" {
		t.Fatalf("equipamento local não atualizado: %+v", d)
	}
	isNew, err := f.c.register(ctx, discovery.Found{Target: discovery.Target{IP: "10.0.0.1", Port: 161}, CredentialID: "public"})
	if err != nil || isNew {
		t.Fatalf("mesmo serial não é novo: %v %v", isNew, err)
	}
}

func TestNoRangeSuggestsPrivateSubnetsOnce(t *testing.T) {
	f := newFixture(t)
	var calls [][]string
	fail := true
	f.c.d.LocalIPs = func() []string { return []string{"192.168.10.20", "8.8.8.8", "10.1.2.3"} }
	f.c.d.Suggest = func(_ context.Context, r []string) error {
		calls = append(calls, r)
		if fail {
			return errors.New("servidor fora do ar")
		}
		return nil
	}
	if err := f.c.Apply(f.config(t)); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	f.c.scan(ctx) // falha: tenta de novo na próxima varredura
	fail = false
	f.c.scan(ctx)
	f.c.scan(ctx) // já sugerido: não repete
	if len(calls) != 2 || !slices.Equal(calls[1], []string{"10.1.2.0/24", "192.168.10.0/24"}) {
		t.Fatalf("sugestões: %v", calls)
	}
	if f.net.dials != 0 {
		t.Fatal("sem faixa aprovada nada pode ser varrido")
	}
	f.c.d.LocalIPs = func() []string { return []string{"8.8.8.8"} }
	f.c.suggested = ""
	f.c.scan(ctx)
	if len(calls) != 2 {
		t.Fatal("sem rede privada não há o que sugerir")
	}
}

func TestApplyDefaultsAndInvalidProfiles(t *testing.T) {
	f := newFixture(t)
	cfg := &protocol.AgentConfig{
		Profiles:  []json.RawMessage{json.RawMessage(`{"id":"x"}`), json.RawMessage(`nada`)},
		Discovery: protocol.DiscoveryConfig{Retries: -1},
		Paused:    true,
	}
	err := f.c.Apply(cfg)
	if err == nil || len(f.c.Profiles()) != 0 {
		t.Fatalf("perfis inválidos deveriam ser ignorados com erro: %v", err)
	}
	if f.c.intervals != DefaultIntervals || f.c.disc != DefaultDiscovery || !f.c.Paused() || f.c.Role() != "standby" {
		t.Fatalf("padrões: %+v %+v", f.c.intervals, f.c.disc)
	}
	if _, ok := f.c.credential("x"); ok {
		t.Fatal("sem credenciais")
	}
	if err := f.c.readTasks(context.Background(), &store.Device{IP: "10.0.0.1", Port: 161}, []string{TaskCounters}); err == nil {
		t.Fatal("ler sem credencial deveria falhar")
	}
}

func TestNewCredentialOrRangeTriggersRescan(t *testing.T) {
	f := newFixture(t)
	cfg := f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32"})
	cfg.Credentials = nil
	apply := func(cfg *protocol.AgentConfig) bool {
		t.Helper()
		f.c.scanReq.Store(false)
		if err := f.c.Apply(cfg); err != nil {
			t.Fatal(err)
		}
		return f.c.scanReq.Load()
	}
	if !apply(cfg) {
		t.Fatal("primeira configuração deve varrer")
	}
	same := *cfg
	same.ConfigVersion++
	same.Intervals.CountersMinutes = 5
	if apply(&same) {
		t.Fatal("mudança só de intervalos não precisa varrer de novo")
	}
	// Caso real do fluxo manual: o local não tinha credencial; o portal adicionou "public".
	withCred := same
	withCred.Credentials = []snmp.Credential{{ID: "public", Version: "v2c", Community: "public"}}
	if !apply(&withCred) {
		t.Fatal("credencial nova deve disparar varredura imediata")
	}
	otherRange := withCred
	otherRange.Ranges = []protocol.IPRange{{ID: "r1", CIDR: "10.0.0.0/30"}}
	if !apply(&otherRange) {
		t.Fatal("faixa alterada deve disparar varredura imediata")
	}
}

func TestFallbackSerialAndRefFromStore(t *testing.T) {
	f := newFixture(t)
	if err := f.c.Apply(f.config(t, protocol.IPRange{ID: "r1", CIDR: "10.0.0.1/32"})); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	// Impressora sem serial no SNMP: identidade estável pelo MAC.
	f.net.set("10.0.0.1:161", "05-generica")
	src := f.net.hosts["10.0.0.1:161"]
	var pdus []snmp.PDU
	_ = src.Walk(ctx, "1.3", func(p snmp.PDU) error {
		if p.OID != "1.3.6.1.2.1.43.5.1.1.17.1" {
			pdus = append(pdus, p)
		}
		return nil
	})
	f.net.hosts["10.0.0.1:161"] = snmp.NewMemSource(pdus)
	if _, err := f.c.register(ctx, discovery.Found{Target: discovery.Target{IP: "10.0.0.1", Port: 161}, CredentialID: "public"}); err != nil {
		t.Fatal(err)
	}
	d, err := f.st.DeviceAt(ctx, "10.0.0.1", 161)
	if err != nil || d.Serial != "MAC-00155D000005" {
		t.Fatalf("serial pelo MAC: %+v %v", d, err)
	}
	ref := refFromStore(*d)
	if ref.MAC != "00:15:5D:00:00:05" || ref.Serial != d.Serial || ref.Hostname != "SIM-GENERICA" {
		t.Fatalf("referência: %+v", ref)
	}
	// Sem serial e sem MAC não dá para identificar.
	var noID []snmp.PDU
	for _, p := range pdus {
		if p.OID != "1.3.6.1.2.1.2.2.1.6.1" {
			noID = append(noID, p)
		}
	}
	f.net.hosts["10.0.0.2:161"] = snmp.NewMemSource(noID)
	if _, err := f.c.register(ctx, discovery.Found{Target: discovery.Target{IP: "10.0.0.2", Port: 161}, CredentialID: "public"}); err == nil {
		t.Fatal("impressora sem serial nem MAC não pode ser registrada")
	}
	f.net.off("10.0.0.2:161")
}
