package usbprint

import (
	"bytes"
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"
)

func TestIdentityAndPaths(t *testing.T) {
	hp := Printer{
		Name: "HP LaserJet M15w", Driver: "HP LaserJet M14-M17 PCLm-S", Port: "USB001",
		PNPDeviceID: `USBPRINT\HPLASERJET_M15W\7&2A7B3C&0&USB001`, Parent: `USB\VID_03F0&PID_8D2A\VNC3K12345`,
	}
	if got := hp.InterfacePath(); got != `\\?\USB#VID_03F0&PID_8D2A#VNC3K12345#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}` {
		t.Fatalf("caminho da interface: %s", got)
	}
	if hp.Serial("PC-01") != "VNC3K12345" || hp.Brand() != "HP" || hp.Model() != "HP LaserJet M14-M17 PCLm-S" {
		t.Fatalf("identidade: %s %s %s", hp.Serial("PC-01"), hp.Brand(), hp.Model())
	}
	// Sem serial USB (Windows inventa um id com '&'): identidade estável por PC + impressora.
	noSerial := Printer{Name: "Epson L3150", PNPDeviceID: `USBPRINT\EPSONL3150\8&1&0&USB002`, Parent: `USB\VID_04B8&PID_1162\5&3A&0&2`}
	a, b := noSerial.Serial("PC-01"), noSerial.Serial("PC-01")
	if a != b || !strings.HasPrefix(a, "USB-") || len(a) != 16 || a == noSerial.Serial("PC-02") {
		t.Fatalf("serial sintético: %s %s", a, noSerial.Serial("PC-02"))
	}
	if noSerial.Brand() != "Epson" || (Printer{}).InterfacePath() != "" {
		t.Fatal("marca/caminho vazio")
	}
}

func TestParsePJL(t *testing.T) {
	for _, resp := range []string{
		"@PJL INFO PAGECOUNT\r\n12345\r\n\f",
		"@PJL INFO PAGECOUNT\r\nPAGECOUNT=12345\r\n\f",
		"lixo@PJL INFO PAGECOUNT\n  12345\n\f",
	} {
		n, err := ParsePageCount([]byte(resp))
		if err != nil || n != 12345 {
			t.Fatalf("%q: %d %v", resp, n, err)
		}
	}
	if _, err := ParsePageCount([]byte("@PJL INFO STATUS\r\nCODE=10001\r\n\f")); err == nil {
		t.Fatal("sem PAGECOUNT deveria falhar")
	}
	if got := ParseID([]byte("@PJL INFO ID\r\n\"HP LaserJet M15w\"\r\n\f")); got != "HP LaserJet M15w" {
		t.Fatalf("ID: %q", got)
	}
}

// fakePort answers PJL like a printer (optionally in pieces, or never).
type fakePort struct {
	sent   bytes.Buffer
	answer []string
	block  chan struct{}
}

func (f *fakePort) Write(p []byte) (int, error) { return f.sent.Write(p) }
func (f *fakePort) Read(p []byte) (int, error) {
	if len(f.answer) == 0 {
		if f.block != nil {
			<-f.block
		}
		return 0, io.EOF
	}
	n := copy(p, f.answer[0])
	f.answer = f.answer[1:]
	return n, nil
}

func TestQueryOverFakePort(t *testing.T) {
	port := &fakePort{answer: []string{"@PJL INFO ID\r\n\"Brother HL-1212W\"\r\n\f@PJL INFO PAGE", "COUNT\r\n4321\r\n\f"}}
	n, model, err := Query(context.Background(), port)
	if err != nil || n != 4321 || model != "Brother HL-1212W" {
		t.Fatalf("consulta: %d %q %v", n, model, err)
	}
	if !strings.HasPrefix(port.sent.String(), "\x1b%-12345X@PJL\r\n@PJL INFO ID\r\n@PJL INFO PAGECOUNT\r\n") {
		t.Fatalf("pedido PJL: %q", port.sent.String())
	}
	// Impressora que não responde (GDI): desiste no prazo, com ErrNoAnswer.
	silent := &fakePort{block: make(chan struct{})}
	defer close(silent.block)
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if _, _, err := Query(ctx, silent); !errors.Is(err, ErrNoAnswer) {
		t.Fatalf("sem resposta deveria ser ErrNoAnswer: %v", err)
	}
	// Responde outra coisa e fecha.
	if _, _, err := Query(context.Background(), &fakePort{answer: []string{"?"}}); !errors.Is(err, ErrNoAnswer) {
		t.Fatalf("resposta inválida: %v", err)
	}
}

// Filas de impressoras desconectadas há tempo somem; duas filas na mesma porta viram uma (sem a cópia).
func TestConnectedDropsGhostsAndDuplicates(t *testing.T) {
	list := []Printer{
		{Name: "HP LaserJet (Cópia 1)", Port: "USB005", Present: true},
		{Name: "HP LaserJet", Port: "USB005", Present: true, Parent: `USB\VID_03F0&PID_8D2A\VNC3K1`},
		{Name: "Epson L3150", Port: "usb006", Present: true},
		{Name: "Epson L3150 (Copy 1)", Port: "USB006", Present: true},
		{Name: "Antiga", Port: "USB001", Present: false},
		{Name: "Antiga 2", Port: "USB002", Present: false},
	}
	got := Connected(list)
	if len(got) != 2 {
		t.Fatalf("esperava 2 impressoras ligadas: %+v", got)
	}
	if got[0].Name != "HP LaserJet" || got[0].Parent == "" {
		t.Fatalf("USB005 deveria ficar com a fila original: %+v", got[0])
	}
	if got[1].Name != "Epson L3150" {
		t.Fatalf("USB006: %+v", got[1])
	}
}
