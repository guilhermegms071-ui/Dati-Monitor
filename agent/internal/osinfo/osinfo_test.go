package osinfo

import (
	"net"
	"runtime"
	"slices"
	"testing"
	"time"
)

func TestPrivateSubnets24(t *testing.T) {
	got := PrivateSubnets24([]string{"192.168.1.20", "192.168.1.30", "10.0.5.1", "172.16.9.9", "8.8.8.8", "fe80::1", "lixo"})
	if !slices.Equal(got, []string{"10.0.5.0/24", "172.16.9.0/24", "192.168.1.0/24"}) {
		t.Fatalf("%v", got)
	}
	if PrivateSubnets24(nil) != nil {
		t.Fatal("vazio")
	}
}

func TestHostDescription(t *testing.T) {
	if Hostname() == "" {
		t.Fatal("Hostname vazio")
	}
	want := "linux"
	if runtime.GOOS == "windows" {
		want = "windows"
	}
	if Kind() != want {
		t.Fatal("Kind")
	}
	for _, ip := range LocalIPv4() {
		if p := net.ParseIP(ip); p == nil || p.To4() == nil || p.IsLoopback() {
			t.Fatalf("IP local inválido: %s", ip)
		}
	}
	if mac := HostMAC(); mac != "" {
		if _, err := net.ParseMAC(mac); err != nil {
			t.Fatalf("MAC: %s", mac)
		}
	}
	if Describe() == "" {
		t.Fatal("Describe vazio")
	}
	if err := CheckSupported(); err != nil {
		t.Fatalf("este PC roda os testes, então é suportado: %v", err)
	}
}

func TestProcessMeter(t *testing.T) {
	var m ProcessMeter
	cpu, mem := m.Sample()
	if cpu != 0 || mem == 0 {
		t.Fatalf("primeira amostra: %v %v", cpu, mem)
	}
	deadline := time.Now().Add(50 * time.Millisecond)
	x := 0
	for time.Now().Before(deadline) {
		x++
	}
	cpu, _ = m.Sample()
	if cpu < 0 || cpu > 100 || x == 0 {
		t.Fatalf("CPU fora de 0..100: %v", cpu)
	}
}
