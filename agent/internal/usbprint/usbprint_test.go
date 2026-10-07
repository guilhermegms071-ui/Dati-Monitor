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

// Só o que o Windows diz que está ligado agora conta, com o caminho atual (o do registro pode ser antigo).
func TestApplyPresentUsesTheLivePath(t *testing.T) {
	live := `\\?\USB#VID_132B&PID_236C#000DE90C#{28d78fad-5a12-11d1-ae5b-0000f803a8c2}`
	list := []Printer{
		{Name: "KONICA USB", Port: "usb003", Offline: true, Parent: `USB\VID_04B8&PID_118A&MI_01\6&592CD5&1&0001`},
		{Name: "Fila antiga", Port: "USB004", Offline: true},
		{Name: "Outra antiga", Port: "USB007"},
	}
	got := Connected(ApplyPresent(list, map[string]string{"USB003": live}))
	if len(got) != 1 {
		t.Fatalf("só a impressora ligada deveria sobrar: %+v", got)
	}
	k := got[0]
	if !k.Present || k.Offline || k.InterfacePath() != live || k.Parent != `USB\VID_132B&PID_236C\000DE90C` {
		t.Fatalf("impressora ligada: %+v", k)
	}
	if k.Serial("PC") != "000DE90C" {
		t.Fatalf("serial pelo caminho atual: %s", k.Serial("PC"))
	}
	if got := Connected(ApplyPresent(list, map[string]string{})); len(got) != 0 {
		t.Fatalf("nada ligado: %+v", got)
	}
}

// O driver USB responde na hora com 0 bytes enquanto a impressora não tem a resposta: a leitura espera.
func TestQueryWaitsWhileTheDriverReturnsNothing(t *testing.T) {
	port := &slowPort{empty: 5, answer: "@PJL INFO ID\r\n\"bizhub\"\r\n\f@PJL INFO PAGECOUNT\r\n987\r\n\f"}
	n, model, err := Query(context.Background(), port)
	if err != nil || n != 987 || model != "bizhub" {
		t.Fatalf("consulta: %d %q %v", n, model, err)
	}
	if port.reads < 6 {
		t.Fatalf("deveria ler de novo depois das respostas vazias: %d leituras", port.reads)
	}
}

type slowPort struct {
	empty  int
	answer string
	reads  int
}

func (p *slowPort) Write(b []byte) (int, error) { return len(b), nil }

func (p *slowPort) Read(b []byte) (int, error) {
	p.reads++
	if p.empty > 0 {
		p.empty--
		return 0, nil
	}
	if p.answer == "" {
		return 0, io.EOF
	}
	n := copy(b, p.answer)
	p.answer = p.answer[n:]
	return n, nil
}

// Impressora cujo driver não publica a interface USB padrão (algumas Canon): conta como ligada pelo aparelho
// USB presente, sem caminho direto.
func TestApplyPresentKeepsPrinterWithoutStandardInterface(t *testing.T) {
	list := []Printer{
		{Name: "Canon MF", Port: "USB001", Offline: true, DevicePresent: true, Parent: `USB\VID_04A9&PID_18A8&MI_01\6&1EA3F843&0&0001`},
		{Name: "Canon antiga", Port: "USB002", Parent: `USB\VID_04A9&PID_10E3\184CAC`},
	}
	got := Connected(ApplyPresent(list, map[string]string{}))
	if len(got) != 1 || got[0].Name != "Canon MF" || got[0].Offline || got[0].Path != "" {
		t.Fatalf("só a Canon ligada, sem caminho direto: %+v", got)
	}
}

// Só os drivers que indicam PJL recebem o pedido pela fila (os outros poderiam imprimir o pedido como texto).
func TestSpoolerSafe(t *testing.T) {
	for driver, want := range map[string]bool{
		"Canon Generic Plus UFR II":      true,
		"Canon MF3010 UFRII LT":          false,
		"Canon LBP2900 CAPT":             false,
		"HP Universal Printing PCL 6":    true,
		"KONICA MINOLTA C4000iSeriesPCL": true,
		"Kyocera ECOSYS M3550idn KX":     true,
		"Generic / Text Only":            false,
		"EPSON L3150 Series":             false,
		"Brother HL-L2350DW series":      false,
		"Brother HL-L5100DN BR-Script3":  true,
		"Xerox Global Print Driver PS":   true,
		"Microsoft Print To PDF":         false,
	} {
		if got := SpoolerSafe(driver); got != want {
			t.Errorf("%s: %v, esperava %v", driver, got, want)
		}
	}
}

// A fila do fax da multifuncional (outra porta USB) é a mesma impressora: não vira um equipamento a mais.
func TestConnectedSkipsFaxQueue(t *testing.T) {
	list := []Printer{
		{Name: "Canon GX7000 series (Copiar 4)", Driver: "Canon GX7000 series", Port: "USB007", Present: true},
		{Name: "Canon GX7000 series FAX (Copiar 3)", Driver: "Canon GX7000 series FAX", Port: "USB008", Present: true},
	}
	got := Connected(list)
	if len(got) != 1 || got[0].Port != "USB007" {
		t.Fatalf("só a impressora, sem o fax: %+v", got)
	}
}

func TestDeviceID(t *testing.T) {
	id := "MFG:KONICA MINOLTA;CMD:PJL,PCL5c,PCLXL,POSTSCRIPT;MDL:bizhub C3320i;CLS:PRINTER;SN:00A52E67;"
	f := ParseDeviceID(id)
	if f["MFG"] != "KONICA MINOLTA" || f["MDL"] != "bizhub C3320i" || f["SN"] != "00A52E67" {
		t.Fatalf("campos: %v", f)
	}
	if got := DescribeDeviceID(id); got != "KONICA MINOLTA bizhub C3320i; fala PJL (PJL,PCL5c,PCLXL,POSTSCRIPT)" {
		t.Fatal(got)
	}
	inkjet := "MANUFACTURER:Canon;MODEL:GX7000 series;COMMAND SET:BJL,BJRaster3,IVEC;"
	if got := DescribeDeviceID(inkjet); got != "Canon GX7000 series; NÃO fala PJL (BJL,BJRaster3,IVEC)" {
		t.Fatal(got)
	}
	if DescribeDeviceID("") != "" {
		t.Fatal("vazio")
	}
}
