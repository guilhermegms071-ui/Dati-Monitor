package printer

import (
	"context"
	"reflect"
	"slices"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

func TestReadAttributesFromSimulatedKonica(t *testing.T) {
	ctx := context.Background()
	src := sim(t, "03-konica-cor")
	id, p, err := ReadIdentity(ctx, src, profiles(t))
	if err != nil {
		t.Fatal(err)
	}
	a, err := ReadAttributes(ctx, src, p, id)
	if err != nil {
		t.Fatal(err)
	}
	if a.MemoryBytes == nil || *a.MemoryBytes != 2097152*1024 {
		t.Fatalf("memória: %v", a.MemoryBytes)
	}
	if a.UptimeSeconds == nil || *a.UptimeSeconds != 86400 {
		t.Fatalf("uptime: %v", a.UptimeSeconds)
	}
	wantDisk := []Storage{{Description: "HDD", SizeBytes: 61035156 * 4096, UsedBytes: 244140 * 4096}}
	if !reflect.DeepEqual(a.Storage, wantDisk) {
		t.Fatalf("disco: %+v", a.Storage)
	}
	// Firmware do perfil (identidade) primeiro, depois o da ENTITY-MIB, sem repetir.
	if !slices.Equal(a.Firmware, []string{"G00-R5", "Controller 1.20"}) {
		t.Fatalf("firmware: %v", a.Firmware)
	}
	if len(a.Subsystems) != 1 || a.Subsystems[0] != (Subsystem{
		Name: "printer", Description: "KONICA MINOLTA bizhub C287", Status: "running",
	}) {
		t.Fatalf("subsistemas: %+v", a.Subsystems)
	}
	if a.PanelText != "Pronto para copiar" || a.SysLocation != "Simulador Dati Monitor" || a.MAC != "00:AA:00:00:00:03" {
		t.Fatalf("atributos: %+v", a)
	}
	if a.SSID != "" || len(a.Parts) != 0 {
		t.Fatalf("sem perfil de atributos não há SSID nem peças: %+v", a)
	}
}

func TestProfileAttributesSSIDSubsystemsAndParts(t *testing.T) {
	src := snmp.NewMemSource([]snmp.PDU{
		{OID: "1.9.1.0", Kind: snmp.KindOctetString, Bytes: []byte("REDE-CLIENTE")},
		{OID: "1.9.2.0", Kind: snmp.KindOctetString, Bytes: []byte("Pronta")},
		{OID: "1.9.3.0", Kind: snmp.KindOctetString, Bytes: []byte("Erro de leitura")},
		{OID: "1.9.4.0", Kind: snmp.KindInteger, Int: 64},
		{OID: "1.9.5.0", Kind: snmp.KindCounter32, Int: 120000},
	})
	p := &profile.Profile{ID: "t", Version: 1, Attributes: &profile.Attributes{
		SSID: []profile.OIDRef{{OID: profile.Placeholder}, {OID: "1.9.0.0"}, {OID: "1.9.1.0"}},
		Subsystems: &profile.Subsystems{
			Printer: []profile.OIDRef{{OID: "1.9.2.0"}},
			Scanner: []profile.OIDRef{{OID: "1.9.3.0"}},
			Copier:  []profile.OIDRef{{OID: "1.9.9.9"}}, // não responde: fica de fora
		},
		Parts: map[string]profile.Part{
			"drum_k":    {OID: "1.9.4.0", Part: "drum", Unit: "percent", Color: "black"},
			"fuser":     {OID: "1.9.5.0", Part: "fuser", Unit: "pages"},
			"transfer":  {OID: profile.Placeholder, Part: "transfer", Unit: "percent"},
			"not_there": {OID: "1.9.8.0", Part: "rollers", Unit: "count"},
		},
	}}
	a, err := ReadAttributes(context.Background(), src, p, Identity{})
	if err != nil {
		t.Fatal(err)
	}
	if a.SSID != "REDE-CLIENTE" {
		t.Fatalf("SSID: %q", a.SSID)
	}
	if !reflect.DeepEqual(a.Subsystems, []Subsystem{{Name: "printer", Status: "Pronta"}, {Name: "scanner", Status: "Erro de leitura"}}) {
		t.Fatalf("subsistemas: %+v", a.Subsystems)
	}
	want := []Part{
		{Name: "drum_k", Part: "drum", Color: "black", Unit: "percent", Value: 64},
		{Name: "fuser", Part: "fuser", Unit: "pages", Value: 120000},
	}
	if !reflect.DeepEqual(a.Parts, want) {
		t.Fatalf("peças: %+v", a.Parts)
	}
}

func TestFullAlertTableAndCartridgeSerials(t *testing.T) {
	ctx := context.Background()
	st, err := ReadStatus(ctx, sim(t, "07-erros"), nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []Alert{
		{Index: 1, Severity: 3, TrainingLevel: 4, Group: 13, GroupIndex: 2, Code: 8,
			Description: "Atolamento de papel na bandeja 2", Time: 8630000},
		{Index: 2, Severity: 3, TrainingLevel: 3, Group: 6, GroupIndex: 1, Code: 3,
			Description: "Porta frontal aberta", Time: 8635000},
	}
	if !reflect.DeepEqual(st.Alerts, want) {
		t.Fatalf("alertas: %+v", st.Alerts)
	}

	sup := []Supply{{Key: "1.1"}, {Key: "1.2"}, {Key: "1.3"}}
	src := snmp.NewMemSource([]snmp.PDU{
		{OID: "1.8.1.1", Kind: snmp.KindOctetString, Bytes: []byte("CART-K-001")},
		{OID: "1.8.1.2", Kind: snmp.KindOctetString, Bytes: []byte(" ")},
	})
	if err := AttachCartridgeSerials(ctx, src, ".1.8", sup); err != nil {
		t.Fatal(err)
	}
	if sup[0].CartridgeSerial != "CART-K-001" || sup[1].CartridgeSerial != "" || sup[2].CartridgeSerial != "" {
		t.Fatalf("seriais: %+v", sup)
	}
}
