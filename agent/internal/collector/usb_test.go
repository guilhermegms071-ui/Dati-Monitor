package collector

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/usbprint"
)

// TestScanUSB: every USB printer gets a status item (source=usb); the one that answers PJL also gets a
// reading with the page counter; the offline one is not queried; a PJL failure is not an error item.
func TestScanUSB(t *testing.T) {
	f := newFixture(t)
	queried := map[string]bool{}
	deps := USBDeps{
		Hostname: func() string { return "PC-RECEPCAO" },
		List: func(context.Context) ([]usbprint.Printer, error) {
			return []usbprint.Printer{
				{Name: "HP LaserJet M15w", Driver: "HP LaserJet M14-M17", Port: "USB001", Parent: `USB\VID_03F0&PID_8D2A\VNC3K1`},
				{Name: "Epson L3150", Driver: "EPSON L3150 Series", Port: "USB002", PNPDeviceID: `USBPRINT\E\1`, Parent: `USB\VID_04B8&PID_1\5&3&0&2`},
				{Name: "Brother HL", Driver: "Brother HL-1212W", Port: "USB003", Parent: `USB\VID_04F9&PID_1\BRX9`, Offline: true},
			}, nil
		},
		PageCount: func(_ context.Context, p usbprint.Printer) (int64, string, error) {
			queried[p.Port] = true
			if p.Port == "USB001" {
				return 12345, "HP LaserJet M15w", nil
			}
			return 0, "", usbprint.ErrNoAnswer
		},
	}
	if n := f.c.ScanUSB(context.Background(), deps); n != 3 {
		t.Fatalf("impressoras reportadas: %d", n)
	}
	if queried["USB003"] || !queried["USB001"] || !queried["USB002"] {
		t.Fatalf("PJL só para as online: %v", queried)
	}
	items := f.items(t)
	if len(filter(items, "status", "")) != 3 {
		t.Fatalf("um status por impressora: %+v", items)
	}
	readings := filter(items, "reading", "")
	if len(readings) != 1 {
		t.Fatalf("leituras: %+v", readings)
	}
	r := readings[0]
	if r.Device.Serial != "VNC3K1" || r.Device.Source != "usb" || r.Device.Brand != "HP" || r.Device.Hostname != "PC-RECEPCAO" {
		t.Fatalf("referência USB: %+v", r.Device)
	}
	if r.Reading.Counters["total"] != 12345 || r.Reading.Source != "usb" || r.Reading.CounterSource != "pjl" {
		t.Fatalf("leitura PJL: %+v", r.Reading)
	}
	off := filter(items, "status", "BRX9")
	if len(off) != 1 || off[0].Status.Status != "offline" {
		t.Fatalf("impressora offline: %+v", off)
	}
	epson := filter(items, "status", "")
	found := false
	for _, it := range epson {
		if it.Device.Brand == "Epson" && len(it.Device.Serial) == 16 {
			found = true
		}
	}
	if !found {
		t.Fatal("Epson sem serial USB deveria ter identidade sintética USB-…")
	}
}

func TestScanUSBListFailureIsLogged(t *testing.T) {
	f := newFixture(t)
	n := f.c.ScanUSB(context.Background(), USBDeps{
		Hostname: func() string { return "PC" },
		List:     func(context.Context) ([]usbprint.Printer, error) { return nil, errors.New("WMI indisponível") },
	})
	if n != 0 || len(f.items(t)) != 0 {
		t.Fatal("falha do WMI não gera itens")
	}
}

func TestRunUSBStopsWithContext(t *testing.T) {
	f := newFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	calls := 0
	go func() {
		f.c.RunUSB(ctx, USBDeps{
			Hostname: func() string { return "PC" },
			List: func(context.Context) ([]usbprint.Printer, error) {
				calls++
				return nil, nil
			},
		})
		close(done)
	}()
	time.Sleep(100 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("RunUSB não parou com o contexto")
	}
	if calls != 1 {
		t.Fatalf("primeiro ciclo logo na partida: %d", calls)
	}
}

// "Ler agora" das USB funciona em qualquer coletor (o STANDBY também: só este PC vê as suas) e devolve o
// resultado de cada impressora; com seriais, lê só essas.
func TestReadUSBNowOnStandby(t *testing.T) {
	f := newFixture(t)
	f.c.mu.Lock()
	f.c.role = "standby"
	f.c.cfg = &protocol.AgentConfig{ClusterRole: "standby"}
	f.c.mu.Unlock()
	deps := USBDeps{
		Hostname: func() string { return "PC-RECEPCAO" },
		List: func(context.Context) ([]usbprint.Printer, error) {
			return []usbprint.Printer{
				{Name: "HP", Driver: "HP LaserJet M14-M17", Port: "USB001", Parent: `USB\VID_03F0&PID_8D2A\VNC3K1`},
				{Name: "Brother HL", Driver: "Brother HL-1212W", Port: "USB003", Parent: `USB\VID_04F9&PID_1\BRX9`, Offline: true},
			}, nil
		},
		PageCount: func(context.Context, usbprint.Printer) (int64, string, error) { return 500, "", nil },
	}
	all, err := f.c.ReadUSBNow(context.Background(), deps, nil)
	if err != nil || len(all) != 2 {
		t.Fatalf("todas as USB: %+v %v", all, err)
	}
	if !all[0].OK || all[0].Serial != "VNC3K1" || all[0].IP != "USB USB001" {
		t.Fatalf("HP: %+v", all[0])
	}
	if all[1].OK || all[1].Error == "" {
		t.Fatalf("Brother desligada: %+v", all[1])
	}
	one, err := f.c.ReadUSBNow(context.Background(), deps, []string{"VNC3K1"})
	if err != nil || len(one) != 1 || one[0].Serial != "VNC3K1" {
		t.Fatalf("só a escolhida: %+v %v", one, err)
	}
	if _, err := f.c.ReadNow(context.Background(), nil); !errors.Is(err, ErrNotMaster) {
		t.Fatalf("as de rede continuam só com o MASTER: %v", err)
	}
}
