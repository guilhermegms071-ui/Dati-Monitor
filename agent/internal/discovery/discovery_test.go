package discovery

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

func TestExpandRanges(t *testing.T) {
	got, err := Expand([]protocol.IPRange{
		{CIDR: "192.168.0.0/29", Exclusions: []string{"192.168.0.3", "192.168.0.5-192.168.0.6"}},
		{Start: "10.0.0.10", End: "10.0.0.11", Ports: []int{161, 1161}},
		{CIDR: "192.168.0.4/32"}, // repetido: não duplica
		{CIDR: "172.16.0.0/31"},
	})
	if err != nil {
		t.Fatal(err)
	}
	var s []string
	for _, x := range got {
		s = append(s, x.String())
	}
	want := "10.0.0.10:161,10.0.0.10:1161,10.0.0.11:161,10.0.0.11:1161,172.16.0.0:161,172.16.0.1:161," +
		"192.168.0.1:161,192.168.0.2:161,192.168.0.4:161"
	if strings.Join(s, ",") != want {
		t.Fatalf("got %s", strings.Join(s, ","))
	}
	bad := []protocol.IPRange{
		{CIDR: "x"}, {CIDR: "10.0.0.0/8"}, {Start: "10.0.0.5", End: "10.0.0.1"}, {},
		{CIDR: "10.0.0.0/30", Exclusions: []string{"nope"}}, {CIDR: "10.0.0.0/30", Ports: []int{0}},
		{Start: "10.0.0.1", End: "fe80::1"},
	}
	for _, r := range bad {
		if _, err := Expand([]protocol.IPRange{r}); err == nil {
			t.Errorf("%+v deveria falhar", r)
		}
	}
	excl, err := Expand([]protocol.IPRange{{CIDR: "10.1.0.0/24", Exclusions: []string{"10.1.0.0/25"}}})
	if err != nil || len(excl) != 127 { // .128 a .254 (a /25 inferior foi excluída)
		t.Fatalf("exclusão por CIDR: %d %v", len(excl), err)
	}
}

// fakeNet answers like a network: some IPs are printers (with a given community), one is a router.
type fakeNet struct {
	printers map[string]string // ip -> community
	router   string
	dials    atomic.Int64
	delay    time.Duration
}

type fakeConn struct {
	*snmp.MemSource
	ok bool
}

func (f fakeConn) Get(ctx context.Context, oids []string) ([]snmp.PDU, error) {
	if !f.ok {
		return nil, snmp.ErrTimeout
	}
	return f.MemSource.Get(ctx, oids)
}
func (fakeConn) Close() error { return nil }

func (n *fakeNet) dial(host string, _ int, cred snmp.Credential, _ snmp.Options) (Conn, error) {
	n.dials.Add(1)
	if n.delay > 0 {
		time.Sleep(n.delay)
	}
	if host == n.router {
		return fakeConn{snmp.NewMemSource([]snmp.PDU{{OID: "1.3.6.1.2.1.1.2.0", Kind: snmp.KindOID, Text: "1.3.6.1.4.1.9.1"}}), true}, nil
	}
	community, isPrinter := n.printers[host]
	if !isPrinter || community != cred.Community {
		return fakeConn{snmp.NewMemSource(nil), false}, nil
	}
	return fakeConn{snmp.NewMemSource([]snmp.PDU{
		{OID: "1.3.6.1.2.1.1.2.0", Kind: snmp.KindOID, Text: "1.3.6.1.4.1.1602.4.7"},
		{OID: "1.3.6.1.2.1.25.3.2.1.2.1", Kind: snmp.KindOID, Text: "1.3.6.1.2.1.25.3.1.5"},
		{OID: "1.3.6.1.2.1.43.5.1.1.17.1", Kind: snmp.KindOctetString, Bytes: []byte("SER-" + host)},
	}), true}, nil
}

func TestScanFindsPrintersWithTheRightCredential(t *testing.T) {
	net := &fakeNet{printers: map[string]string{"10.0.0.5": "public", "10.0.0.9": "segredo"}, router: "10.0.0.1"}
	targets, _ := Expand([]protocol.IPRange{{CIDR: "10.0.0.0/28"}})
	sc := &Scanner{
		Credentials: []snmp.Credential{{ID: "c1", Community: "public"}, {ID: "c2", Community: "segredo"}},
		Concurrency: 8, RatePPS: 10000, Dial: net.dial,
	}
	var found []Found
	probed, err := sc.Scan(context.Background(), targets, func(f Found) { found = append(found, f) })
	if err != nil || probed != len(targets) {
		t.Fatalf("probed=%d err=%v", probed, err)
	}
	creds := map[string]string{}
	for _, f := range found {
		creds[f.Target.IP] = f.CredentialID
		if f.Probe.Serial != "SER-"+f.Target.IP {
			t.Errorf("%+v", f)
		}
	}
	if len(found) != 2 || creds["10.0.0.5"] != "c1" || creds["10.0.0.9"] != "c2" {
		t.Fatalf("encontrados: %v", creds)
	}
	// Com a credencial conhecida, ela é tentada primeiro (1 dial por impressora).
	net.dials.Store(0)
	sc.Known = func(t Target) string { return map[string]string{"10.0.0.9": "c2"}[t.IP] }
	if _, err := sc.Scan(context.Background(), []Target{{IP: "10.0.0.9", Port: 161}}, func(Found) {}); err != nil {
		t.Fatal(err)
	}
	if net.dials.Load() != 1 {
		t.Fatalf("dials=%d", net.dials.Load())
	}
}

func TestScanRateLimitAndCancel(t *testing.T) {
	net := &fakeNet{printers: map[string]string{}}
	targets := make([]Target, 20)
	for i := range targets {
		targets[i] = Target{IP: fmt.Sprintf("10.0.1.%d", i+1), Port: 161}
	}
	sc := &Scanner{Credentials: []snmp.Credential{{ID: "c", Community: "x"}}, Concurrency: 20, RatePPS: 20, Dial: net.dial}
	if _, err := sc.Scan(context.Background(), targets, func(Found) {}); err != nil {
		t.Fatal(err)
	}
	// Com Retries=1 cada alvo custa 2 pacotes: 40 pacotes a 20 pps (rajada de 20) levam ao menos ~1 s.
	sc.Options.Retries = 1
	start := time.Now()
	if _, err := sc.Scan(context.Background(), targets, func(Found) {}); err != nil {
		t.Fatal(err)
	}
	if el := time.Since(start); el < 900*time.Millisecond {
		t.Fatalf("limite de pacotes/s não respeitado: %v", el)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := sc.Scan(ctx, targets, func(Found) {}); !errors.Is(err, context.Canceled) {
		t.Fatalf("esperava cancelamento, veio %v", err)
	}
	if _, err := (&Scanner{}).Scan(context.Background(), targets, func(Found) {}); err == nil {
		t.Fatal("sem credenciais deveria falhar")
	}
}

func TestScanOf254HostsFinishesFast(t *testing.T) {
	// /24 com 64 goroutines: com atraso simulado de 30 ms por host, termina bem abaixo de 30 s.
	net := &fakeNet{printers: map[string]string{"10.2.0.50": "public"}, delay: 30 * time.Millisecond}
	targets, _ := Expand([]protocol.IPRange{{CIDR: "10.2.0.0/24"}})
	sc := &Scanner{Credentials: []snmp.Credential{{ID: "c", Community: "public"}}, Concurrency: 64, RatePPS: 200, Dial: net.dial}
	start := time.Now()
	n := 0
	if _, err := sc.Scan(context.Background(), targets, func(Found) { n++ }); err != nil {
		t.Fatal(err)
	}
	if n != 1 || time.Since(start) > 5*time.Second {
		t.Fatalf("n=%d em %v", n, time.Since(start))
	}
}

func TestExpandSingleHostsAndHostnames(t *testing.T) {
	lookups := 0
	lookup := func(_ context.Context, host string) ([]string, error) {
		lookups++
		switch host {
		case "impressora-rh":
			return []string{"10.0.0.7", "fe80::1"}, nil // IPv6 é ignorado
		case "so-ipv6":
			return []string{"fe80::2"}, nil
		}
		return nil, errors.New("no such host")
	}
	got, unresolved, err := ExpandWith(context.Background(), []protocol.IPRange{
		{Host: "10.0.0.9", Ports: []int{161, 1161}},
		{Host: "impressora-rh"},
		{Host: "desligada.local"},
		{Host: "so-ipv6"},
		{Start: "10.0.0.7", End: "10.0.0.7"}, // o mesmo IP do hostname: não duplica
	}, lookup)
	if err != nil {
		t.Fatal(err)
	}
	var s []string
	for _, tg := range got {
		s = append(s, tg.String())
	}
	if strings.Join(s, " ") != "10.0.0.7:161 10.0.0.9:161 10.0.0.9:1161" {
		t.Fatalf("alvos: %v", s)
	}
	// Um nome que não resolve não impede a varredura do resto; IP literal não consulta o DNS.
	if lookups != 3 || len(unresolved) != 2 || !strings.HasPrefix(unresolved[0], "desligada.local: ") {
		t.Fatalf("consultas=%d não resolvidos=%v", lookups, unresolved)
	}
	if _, err := Expand([]protocol.IPRange{{Host: "127.0.0.1"}}); err != nil {
		t.Fatalf("IP avulso: %v", err)
	}
}
