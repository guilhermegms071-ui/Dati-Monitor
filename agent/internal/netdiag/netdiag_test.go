package netdiag

import (
	"bytes"
	"context"
	"net"
	"path/filepath"
	"testing"
	"time"
)

func TestMagicPacket(t *testing.T) {
	pkt, err := MagicPacket("00-11-22-33-44-55")
	if err != nil || len(pkt) != 102 {
		t.Fatalf("%d %v", len(pkt), err)
	}
	if !bytes.Equal(pkt[:6], bytes.Repeat([]byte{0xFF}, 6)) {
		t.Fatal("cabeçalho")
	}
	mac := []byte{0x00, 0x11, 0x22, 0x33, 0x44, 0x55}
	for i := 0; i < 16; i++ {
		if !bytes.Equal(pkt[6+i*6:12+i*6], mac) {
			t.Fatalf("repetição %d", i)
		}
	}
	for _, bad := range []string{"", "00:11:22", "zz:11:22:33:44:55", "00:11:22:33:44:55:66"} {
		if _, err := MagicPacket(bad); err == nil {
			t.Errorf("%q deveria falhar", bad)
		}
	}
}

func TestDirectedBroadcast(t *testing.T) {
	_, n, _ := net.ParseCIDR("192.168.10.20/24")
	n.IP = net.ParseIP("192.168.10.20")
	if got := directedBroadcast(n).String(); got != "192.168.10.255" {
		t.Fatal(got)
	}
	_, n, _ = net.ParseCIDR("10.1.0.0/16")
	if got := directedBroadcast(n).String(); got != "10.1.255.255" {
		t.Fatal(got)
	}
}

func TestWakeOnLANSendsPackets(t *testing.T) {
	// MAC administrado localmente (02:…): não liga nenhuma máquina de verdade.
	sent, err := WakeOnLAN("02:00:5e:00:00:01", []string{"192.168.77.5", "8.8.8.8", "lixo"})
	if err != nil || len(sent) == 0 {
		t.Fatalf("%v %v", sent, err)
	}
	found := false
	for _, s := range sent {
		if s == "192.168.77.255:9" {
			found = true
		}
	}
	if !found {
		t.Fatalf("broadcast da rede do PC de destino ausente: %v", sent)
	}
	if _, err := WakeOnLAN("invalido", nil); err == nil {
		t.Fatal("MAC inválido")
	}
}

func TestTCPPortsOpenAndRefused(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	open := ln.Addr().(*net.TCPAddr).Port
	closedLn, _ := net.Listen("tcp", "127.0.0.1:0")
	closed := closedLn.Addr().(*net.TCPAddr).Port
	_ = closedLn.Close()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			_ = c.Close()
		}
	}()
	defer func() { _ = ln.Close() }()
	res := TCPPorts(context.Background(), "127.0.0.1", []int{open, closed}, 2*time.Second)
	if !res[0].Open || res[1].Open || !res[1].Refused || res[1].Error == "" {
		t.Fatalf("%+v", res)
	}
}

func TestInterfacesAndDisk(t *testing.T) {
	if len(Interfaces()) == 0 {
		t.Fatal("nenhuma interface")
	}
	d, err := DiskUsage(t.TempDir())
	if err != nil || d.TotalBytes == 0 || d.FreeBytes > d.TotalBytes {
		t.Fatalf("%+v %v", d, err)
	}
	if _, err := DiskUsage(filepath.Join(t.TempDir(), "nao", "existe")); err == nil {
		t.Fatal("caminho inexistente")
	}
}

func TestICMPInvalidIP(t *testing.T) {
	if r := ICMP(context.Background(), "abc", 1, time.Second); r.Error == "" || r.Sent != 0 {
		t.Fatalf("%+v", r)
	}
}
