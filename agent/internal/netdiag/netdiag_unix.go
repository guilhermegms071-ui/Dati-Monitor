//go:build !windows

package netdiag

import (
	"errors"
	"fmt"
	"net"
	"os"
	"time"

	"golang.org/x/net/icmp"
	"golang.org/x/net/ipv4"
)

// icmpEcho tries the unprivileged ICMP socket ("udp4", allowed by net.ipv4.ping_group_range) and
// falls back to the raw socket (the service runs as root).
func icmpEcho(ip net.IP, timeout time.Duration, seq int) (time.Duration, error) {
	var lastErr error
	for _, network := range []string{"udp4", "ip4:icmp"} {
		rtt, err := echoOn(network, ip, timeout, seq)
		if err == nil {
			return rtt, nil
		}
		lastErr = err
		var opErr *net.OpError
		if errors.As(err, &opErr) && opErr.Op == "listen" {
			continue // sem permissão para este tipo de socket: tenta o próximo
		}
		return 0, err
	}
	return 0, fmt.Errorf("ICMP indisponível neste sistema: %w", lastErr)
}

func echoOn(network string, ip net.IP, timeout time.Duration, seq int) (time.Duration, error) {
	conn, err := icmp.ListenPacket(network, "0.0.0.0")
	if err != nil {
		return 0, err
	}
	defer func() { _ = conn.Close() }()
	id := os.Getpid() & 0xffff
	msg := icmp.Message{Type: ipv4.ICMPTypeEcho, Code: 0, Body: &icmp.Echo{ID: id, Seq: seq, Data: []byte("dati-monitor-ping")}}
	raw, err := msg.Marshal(nil)
	if err != nil {
		return 0, err
	}
	var dst net.Addr = &net.IPAddr{IP: ip}
	if network == "udp4" {
		dst = &net.UDPAddr{IP: ip}
	}
	start := time.Now()
	if _, err := conn.WriteTo(raw, dst); err != nil {
		return 0, err
	}
	if err := conn.SetReadDeadline(time.Now().Add(timeout)); err != nil {
		return 0, err
	}
	buf := make([]byte, 1500)
	for {
		n, _, err := conn.ReadFrom(buf)
		if err != nil {
			return 0, errors.New("sem resposta (tempo esgotado)")
		}
		reply, err := icmp.ParseMessage(1, buf[:n])
		if err != nil {
			continue
		}
		if echo, ok := reply.Body.(*icmp.Echo); ok && reply.Type == ipv4.ICMPTypeEchoReply && echo.Seq == seq {
			return time.Since(start), nil
		}
	}
}
