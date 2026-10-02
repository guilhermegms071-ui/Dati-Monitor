//go:build windows

package usbprint

import (
	"context"
	"testing"
)

func TestParseList(t *testing.T) {
	one := `{"name":"HP","driver":"HP LJ","port":"USB001","pnp_device_id":"USBPRINT\\HP\\1","parent":"USB\\VID_1&PID_2\\ABC","offline":false}`
	got, err := parseList([]byte("[" + one + "]"))
	if err != nil || len(got) != 1 || got[0].Port != "USB001" || got[0].Parent != `USB\VID_1&PID_2\ABC` {
		t.Fatalf("lista: %+v %v", got, err)
	}
	if got, err := parseList([]byte("")); err != nil || got != nil {
		t.Fatalf("vazio: %+v %v", got, err)
	}
	if _, err := parseList([]byte("{quebrado")); err == nil {
		t.Fatal("JSON inválido deveria falhar")
	}
}

// TestListRunsOnThisPC runs the real WMI query (the PC may have no USB printer: the list is then empty).
func TestListRunsOnThisPC(t *testing.T) {
	printers, err := List(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	for _, p := range printers {
		if p.Port == "" || p.Name == "" {
			t.Fatalf("impressora sem nome/porta: %+v", p)
		}
	}
	t.Logf("%d impressora(s) USB neste PC", len(printers))
}
