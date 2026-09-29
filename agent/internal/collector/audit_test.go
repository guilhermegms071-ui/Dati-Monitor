package collector

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// Datacount audit (PROMPT 16.8, 16.10, 16.1): "monitorar redes conectadas", daily attributes, read
// timeout/retries and discarded serials.
func TestLocalNetworksAttributesAndIgnoredSerials(t *testing.T) {
	ctx := context.Background()
	f := newFixture(t)
	f.c.d.LocalIPs = func() []string { return []string{"10.0.0.50", "8.8.8.8"} }
	suggested := 0
	f.c.d.Suggest = func(context.Context, []string) error { suggested++; return nil }
	f.net.set("10.0.0.1:161", "03-konica-cor")
	f.net.set("10.0.0.2:161", "01-canon-cor")

	// Sem faixa aprovada, mas com "monitorar redes conectadas": varre a /24 do PC (e só ela).
	cfg := f.config(t)
	cfg.MonitorLocalNetworks = true
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	f.c.scan(ctx)
	devs, err := f.st.Devices(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(devs) != 2 || suggested != 0 {
		t.Fatalf("equipamentos=%d sugestões=%d", len(devs), suggested)
	}

	// Leitura diária: atributos padrão + sysLocation na referência do equipamento.
	konica := devs[0]
	if konica.IP != "10.0.0.1" {
		t.Fatalf("ordem inesperada: %+v", devs)
	}
	f.c.mu.Lock()
	f.c.inflight[key(konica.IP, konica.Port)] = true
	f.c.mu.Unlock()
	if err := f.c.readDevice(ctx, konica, []string{TaskAttributes, TaskCounters}); err != nil {
		t.Fatal(err)
	}
	items := f.items(t)
	attrs := filter(items, protocol.KindAttributes, "A797019500624")
	if len(attrs) != 1 {
		t.Fatalf("itens de atributos: %d", len(attrs))
	}
	a := attrs[0].Attributes
	if a.MemoryBytes == nil || *a.MemoryBytes != 2097152*1024 || !slices.Contains(a.Firmware, "Controller 1.20") ||
		len(a.Storage) != 1 || a.SysLocation != "Simulador Dati Monitor" {
		t.Fatalf("atributos: %+v", a)
	}
	reading := filter(items, protocol.KindReading, "A797019500624")
	if len(reading) != 1 || reading[0].Device.SysLocation != "Simulador Dati Monitor" {
		t.Fatalf("leitura: %+v", reading)
	}

	// Descartado em Descobertas: sai da lista local e não volta na varredura seguinte.
	cfg2 := f.config(t)
	cfg2.MonitorLocalNetworks = true
	cfg2.IgnoredSerials = []string{"SIMCAN0001"}
	if err := f.c.Apply(cfg2); err != nil {
		t.Fatal(err)
	}
	f.c.scan(ctx)
	devs, err = f.st.Devices(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(devs) != 1 || devs[0].Serial != "A797019500624" {
		t.Fatalf("depois de descartar: %+v", devs)
	}
}

func TestReadTimeoutAndRetriesCap(t *testing.T) {
	f := newFixture(t)
	cfg := f.config(t)
	cfg.Discovery.Retries = 9
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	if o := f.c.readOptions(); o.Timeout != 2*time.Second || o.Retries != MaxRetries {
		t.Fatalf("leitura: %+v", o)
	}
	if o := f.c.snmpOptions(); o.Timeout != 100*time.Millisecond || o.Retries != MaxRetries {
		t.Fatalf("descoberta: %+v", o)
	}
	cfg.Discovery.ReadTimeoutMS = 3500
	cfg.Discovery.Retries = 2
	if err := f.c.Apply(cfg); err != nil {
		t.Fatal(err)
	}
	if o := f.c.readOptions(); o.Timeout != 3500*time.Millisecond || o.Retries != 2 {
		t.Fatalf("leitura: %+v", o)
	}
	f.c.d.LocalIPs = func() []string { return []string{"8.8.8.8"} }
	if ranges := f.c.localRanges(); len(ranges) != 0 {
		t.Fatalf("sem IPs privados não há redes locais: %+v", ranges)
	}
	f.c.d.LocalIPs = func() []string { return []string{"192.168.5.9", "10.1.1.1"} }
	if ranges := f.c.localRanges(); len(ranges) != 2 || ranges[0].CIDR != "10.1.1.0/24" || ranges[1].Ports[0] != 161 {
		t.Fatalf("redes locais: %+v", ranges)
	}
}
