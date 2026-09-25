//go:build linux

package sdnotify

import (
	"net"
	"path/filepath"
	"testing"
)

func TestSendsToNotifySocket(t *testing.T) {
	path := filepath.Join(t.TempDir(), "notify.sock")
	conn, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = conn.Close() }()
	t.Setenv("NOTIFY_SOCKET", path)
	if err := Ready(); err != nil {
		t.Fatal(err)
	}
	buf := make([]byte, 64)
	n, err := conn.Read(buf)
	if err != nil || string(buf[:n]) != "READY=1" {
		t.Fatalf("%q %v", buf[:n], err)
	}
	t.Setenv("NOTIFY_SOCKET", filepath.Join(t.TempDir(), "nao-existe.sock"))
	if err := Watchdog(); err == nil {
		t.Fatal("socket inexistente deveria dar erro")
	}
}
