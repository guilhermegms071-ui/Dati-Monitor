//go:build integration

package simtest

import (
	"context"
	"errors"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// lossyProxy sits between the collector and snmpsim and lets through only one of every `keep` requests
// (the others are dropped, as on the link of the ECOSYS M3655idn of the real network, Fase 10). It also
// records when each request arrived.
type lossyProxy struct {
	mu       sync.Mutex
	arrivals []time.Time
}

func startLossyProxy(t *testing.T, target int, keep int) (int, *lossyProxy) {
	t.Helper()
	ln, err := net.ListenUDP("udp", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	up, err := net.DialUDP("udp", nil, &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1), Port: target})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = ln.Close(); _ = up.Close() })
	p := &lossyProxy{}
	go func() {
		buf, reply := make([]byte, 65535), make([]byte, 65535)
		for n := 1; ; n++ {
			size, from, err := ln.ReadFromUDP(buf)
			if err != nil {
				return
			}
			p.mu.Lock()
			p.arrivals = append(p.arrivals, time.Now())
			p.mu.Unlock()
			if n%keep != 0 {
				continue // pacote perdido
			}
			if _, err := up.Write(buf[:size]); err != nil {
				return
			}
			_ = up.SetReadDeadline(time.Now().Add(2 * time.Second))
			got, err := up.Read(reply)
			if err != nil {
				continue
			}
			_, _ = ln.WriteToUDP(reply[:got], from)
		}
	}()
	return ln.LocalAddr().(*net.UDPAddr).Port, p
}

// fullRead is what the collector does on each reading: identity, status, supplies and counters.
func fullRead(ctx context.Context, src snmp.Source, ps []*profile.Profile) (int64, error) {
	id, p, err := printer.ReadIdentity(ctx, src, ps)
	if err != nil {
		return 0, err
	}
	if _, err := printer.ReadStatus(ctx, src, p); err != nil {
		return 0, err
	}
	if _, err := printer.ReadSupplies(ctx, src); err != nil {
		return 0, err
	}
	res, err := profile.Evaluate(ctx, src, p, id.Model)
	if err != nil {
		return 0, err
	}
	return res.Counters["total"], nil
}

// Rede com perda alta: com as opções padrão do coletor (1 nova tentativa) a leitura não completa; com o
// ajuste do bloco "snmp" do perfil (mais tentativas curtas) ela completa sempre, com o total exato.
func TestLossyLinkIsReadWithProfileTuning(t *testing.T) {
	sim := Start(t, "05-generica")
	ps := profiles(t)
	ctx := context.Background()

	direct := dial(t, sim, Public)
	want, err := fullRead(ctx, direct, ps)
	if err != nil || want == 0 {
		t.Fatalf("leitura direta: total %d, %v", want, err)
	}

	port, _ := startLossyProxy(t, sim, 3) // 2 de cada 3 pacotes perdidos
	def := snmp.DefaultOptions()
	def.Timeout = 300 * time.Millisecond
	plain, err := snmp.Dial("127.0.0.1", port, Public, def)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = plain.Close() }()
	if _, err := fullRead(ctx, plain, ps); !errors.Is(err, snmp.ErrTimeout) {
		t.Fatalf("com 1 nova tentativa a leitura deveria falhar por timeout, veio %v", err)
	}

	retries, timeout := 6, 300
	tuned := (&profile.Profile{ID: "t", Version: 1, SNMP: &profile.SNMPTuning{
		Models: []profile.SNMPModel{{ModelRegex: "(?i)generic", SNMPValues: profile.SNMPValues{Retries: &retries, TimeoutMS: &timeout}}},
	}}).TuneSNMP(def, "Generic Laser Printer 5000")
	if tuned.Retries != 6 || tuned.Timeout != 300*time.Millisecond {
		t.Fatalf("ajuste do modelo não aplicado: %+v", tuned)
	}
	for i := range 3 {
		c, err := snmp.Dial("127.0.0.1", port, Public, tuned)
		if err != nil {
			t.Fatal(err)
		}
		got, err := fullRead(ctx, c, ps)
		_ = c.Close()
		if err != nil || got != want {
			t.Fatalf("leitura %d com o ajuste: total %d (esperado %d), %v", i+1, got, want, err)
		}
	}
}

// O intervalo entre requisições é respeitado em cada pacote, inclusive nas páginas do GETBULK.
func TestRequestIntervalPacesEveryPacket(t *testing.T) {
	sim := Start(t, "05-generica")
	port, proxy := startLossyProxy(t, sim, 1) // sem perda: só mede a chegada dos pacotes
	o := snmp.DefaultOptions()
	o.RequestInterval, o.MaxRepetitions = 120*time.Millisecond, 2
	c, err := snmp.Dial("127.0.0.1", port, Public, o)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = c.Close() }()
	n := 0
	if err := c.Walk(context.Background(), "1.3.6.1.2.1.43", func(snmp.PDU) error { n++; return nil }); err != nil {
		t.Fatal(err)
	}
	proxy.mu.Lock()
	defer proxy.mu.Unlock()
	if len(proxy.arrivals) < 3 || n == 0 {
		t.Fatalf("walk curto demais para medir: %d pacotes, %d valores", len(proxy.arrivals), n)
	}
	for i := 1; i < len(proxy.arrivals); i++ {
		// folga de 20 ms para o relógio do Windows
		if gap := proxy.arrivals[i].Sub(proxy.arrivals[i-1]); gap < 100*time.Millisecond {
			t.Fatalf("pacotes %d e %d com %v de intervalo (mínimo 120 ms)", i, i+1, gap)
		}
	}
}
