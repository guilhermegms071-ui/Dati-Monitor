// Package sdnotify talks to systemd (Type=notify, WatchdogSec=60 — PROMPT 4.1) without CGO.
// Outside systemd (or on Windows) every call is a no-op.
package sdnotify

import (
	"net"
	"os"
)

// Send writes a notification (e.g. "READY=1", "WATCHDOG=1") to $NOTIFY_SOCKET.
// It returns false when not running under systemd.
func Send(state string) (bool, error) {
	path := os.Getenv("NOTIFY_SOCKET")
	if path == "" {
		return false, nil
	}
	addr := &net.UnixAddr{Name: path, Net: "unixgram"}
	if path[0] == '@' {
		addr.Name = "\x00" + path[1:] // socket abstrato
	}
	conn, err := net.DialUnix("unixgram", nil, addr)
	if err != nil {
		return false, err
	}
	defer func() { _ = conn.Close() }()
	if _, err := conn.Write([]byte(state)); err != nil {
		return false, err
	}
	return true, nil
}

// Ready tells systemd the service finished starting.
func Ready() error { _, err := Send("READY=1"); return err }

// Watchdog pets the systemd watchdog.
func Watchdog() error { _, err := Send("WATCHDOG=1"); return err }

// Stopping tells systemd the service is shutting down.
func Stopping() error { _, err := Send("STOPPING=1"); return err }
