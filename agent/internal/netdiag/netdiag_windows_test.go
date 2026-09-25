//go:build windows

package netdiag

import (
	"context"
	"testing"
	"time"
)

// IcmpSendEcho funciona sem administrador: o loopback sempre responde.
func TestICMPLoopbackWindows(t *testing.T) {
	r := ICMP(context.Background(), "127.0.0.1", 2, 2*time.Second)
	if r.Sent != 2 || r.Received != 2 || len(r.RTTsMS) != 2 || r.Error != "" {
		t.Fatalf("%+v", r)
	}
	// TEST-NET-1 (RFC 5737): nunca responde.
	r = ICMP(context.Background(), "192.0.2.1", 1, 300*time.Millisecond)
	if r.Received != 0 || r.Error == "" {
		t.Fatalf("%+v", r)
	}
}
