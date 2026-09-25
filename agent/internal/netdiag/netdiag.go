// Package netdiag implements the network diagnostics used by remote commands (PROMPT 4.7): ICMP and
// TCP ping, Wake-on-LAN magic packets, the host's interfaces and disk usage.
package netdiag

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"
)

// PingResult is the outcome of an ICMP ping.
type PingResult struct {
	Method   string    `json:"method"`
	Sent     int       `json:"sent"`
	Received int       `json:"received"`
	RTTsMS   []float64 `json:"rtts_ms"`
	Error    string    `json:"error,omitempty"`
}

// ICMP pings ip count times (1 s apart), each with the given timeout.
func ICMP(ctx context.Context, ip string, count int, timeout time.Duration) PingResult {
	res := PingResult{Method: "icmp"}
	addr := net.ParseIP(ip).To4()
	if addr == nil {
		res.Error = "IP inválido: " + ip
		return res
	}
	for i := 0; i < count; i++ {
		if i > 0 {
			select {
			case <-ctx.Done():
				res.Error = ctx.Err().Error()
				return res
			case <-time.After(time.Second):
			}
		}
		res.Sent++
		rtt, err := icmpEcho(addr, timeout, i+1)
		if err != nil {
			res.Error = err.Error()
			continue
		}
		res.Received++
		res.RTTsMS = append(res.RTTsMS, float64(rtt.Microseconds())/1000)
	}
	if res.Received > 0 {
		res.Error = ""
	}
	return res
}

// PortResult is the outcome of a TCP connection attempt.
type PortResult struct {
	Port      int     `json:"port"`
	Open      bool    `json:"open"`
	Refused   bool    `json:"refused"` // o host respondeu (está ligado), mas a porta está fechada
	ElapsedMS float64 `json:"elapsed_ms"`
	Error     string  `json:"error,omitempty"`
}

// TCPPorts tries to connect to each port (works where ICMP is blocked).
func TCPPorts(ctx context.Context, ip string, ports []int, timeout time.Duration) []PortResult {
	out := make([]PortResult, 0, len(ports))
	d := net.Dialer{Timeout: timeout}
	for _, p := range ports {
		start := time.Now()
		conn, err := d.DialContext(ctx, "tcp", net.JoinHostPort(ip, fmt.Sprint(p)))
		r := PortResult{Port: p, ElapsedMS: float64(time.Since(start).Microseconds()) / 1000}
		if err == nil {
			r.Open = true
			_ = conn.Close()
		} else {
			r.Error = err.Error()
			r.Refused = isRefused(err)
		}
		out = append(out, r)
	}
	return out
}

func isRefused(err error) bool {
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "refused") || strings.Contains(msg, "recusou") ||
		strings.Contains(msg, "actively refused")
}

// MagicPacket builds the Wake-on-LAN payload: 6×0xFF followed by the MAC 16 times.
func MagicPacket(mac string) ([]byte, error) {
	clean := strings.NewReplacer(":", "", "-", "", ".", "").Replace(strings.TrimSpace(mac))
	hw, err := hex.DecodeString(clean)
	if err != nil || len(hw) != 6 {
		return nil, fmt.Errorf("MAC inválido: %q", mac)
	}
	pkt := make([]byte, 0, 102)
	for i := 0; i < 6; i++ {
		pkt = append(pkt, 0xFF)
	}
	for i := 0; i < 16; i++ {
		pkt = append(pkt, hw...)
	}
	return pkt, nil
}

// WakeOnLAN sends the magic packet to the limited broadcast and to the directed broadcast of every
// local IPv4 network (and of the target's last known IPs), on UDP ports 9 and 7. Returns where it
// was sent.
func WakeOnLAN(mac string, targetIPs []string) ([]string, error) {
	pkt, err := MagicPacket(mac)
	if err != nil {
		return nil, err
	}
	dests := map[string]bool{"255.255.255.255": true}
	for _, n := range localNets() {
		dests[directedBroadcast(n).String()] = true
	}
	for _, ip := range targetIPs {
		if v4 := net.ParseIP(ip).To4(); v4 != nil && v4.IsPrivate() {
			dests[net.IPv4(v4[0], v4[1], v4[2], 255).String()] = true
		}
	}
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4zero})
	if err != nil {
		return nil, fmt.Errorf("abrir socket UDP: %w", err)
	}
	defer func() { _ = conn.Close() }()
	var sent []string
	var errs []error
	for d := range dests {
		for _, port := range []int{9, 7} {
			addr := &net.UDPAddr{IP: net.ParseIP(d), Port: port}
			if _, err := conn.WriteToUDP(pkt, addr); err != nil {
				errs = append(errs, fmt.Errorf("%s: %w", addr, err))
				continue
			}
			sent = append(sent, addr.String())
		}
	}
	if len(sent) == 0 {
		return nil, fmt.Errorf("nenhum pacote Wake-on-LAN pôde ser enviado: %w", errors.Join(errs...))
	}
	return sent, nil
}

func localNets() []*net.IPNet {
	var out []*net.IPNet
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagUp == 0 || ifc.Flags&net.FlagLoopback != 0 || ifc.Flags&net.FlagBroadcast == 0 {
			continue
		}
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok && n.IP.To4() != nil {
				out = append(out, n)
			}
		}
	}
	return out
}

func directedBroadcast(n *net.IPNet) net.IP {
	ip := n.IP.To4()
	mask := n.Mask
	if len(mask) == net.IPv6len {
		mask = mask[12:]
	}
	b := make(net.IP, 4)
	for i := range b {
		b[i] = ip[i] | ^mask[i]
	}
	return b
}

// Interface describes a network interface of the host.
type Interface struct {
	Name  string   `json:"name"`
	MAC   string   `json:"mac,omitempty"`
	Up    bool     `json:"up"`
	Addrs []string `json:"addrs"`
}

// Interfaces lists the host's interfaces (diagnostics).
func Interfaces() []Interface {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	out := make([]Interface, 0, len(ifaces))
	for _, ifc := range ifaces {
		it := Interface{Name: ifc.Name, MAC: strings.ToUpper(ifc.HardwareAddr.String()), Up: ifc.Flags&net.FlagUp != 0}
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			it.Addrs = append(it.Addrs, a.String())
		}
		out = append(out, it)
	}
	return out
}

// Disk is the usage of the filesystem holding a path.
type Disk struct {
	Path       string `json:"path"`
	TotalBytes uint64 `json:"total_bytes"`
	FreeBytes  uint64 `json:"free_bytes"`
}

// DiskUsage returns the usage of the filesystem holding path.
func DiskUsage(path string) (Disk, error) {
	total, free, err := diskUsage(path)
	if err != nil {
		return Disk{Path: path}, fmt.Errorf("uso de disco de %s: %w", path, err)
	}
	return Disk{Path: path, TotalBytes: total, FreeBytes: free}, nil
}
