// Package discovery expands approved IP ranges and probes them for printers (PROMPT 4.5):
// concurrency-limited, rate-limited (packets/s), trying the site's credentials in order.
package discovery

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"sort"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"

	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// DefaultPort is the SNMP port.
const DefaultPort = 161

// MaxTargets protects against absurd ranges (e.g. a /8 typed by mistake).
const MaxTargets = 65536

// Target is one address to probe.
type Target struct {
	IP   string
	Port int
}

func (t Target) String() string { return net.JoinHostPort(t.IP, fmt.Sprint(t.Port)) }

// LookupFunc resolves a hostname to IPv4 addresses.
type LookupFunc func(ctx context.Context, host string) ([]string, error)

// DefaultLookup uses the system resolver (5 s per name).
func DefaultLookup(ctx context.Context, host string) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	addrs, err := net.DefaultResolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, err
	}
	var out []string
	for _, a := range addrs {
		if v4 := a.IP.To4(); v4 != nil {
			out = append(out, v4.String())
		}
	}
	if len(out) == 0 {
		return nil, errors.New("o nome não tem endereço IPv4")
	}
	return out, nil
}

// Expand turns ranges into a sorted, de-duplicated list of targets. Hostnames are resolved with the
// system resolver; see ExpandWith.
func Expand(ranges []protocol.IPRange) ([]Target, error) {
	targets, unresolved, err := ExpandWith(context.Background(), ranges, DefaultLookup)
	if err == nil && len(unresolved) > 0 {
		return targets, fmt.Errorf("hostnames sem resolução: %s", strings.Join(unresolved, "; "))
	}
	return targets, err
}

// ExpandWith expands ranges resolving single hostnames with `lookup`. A hostname that does not resolve
// does not stop the scan: it is returned in `unresolved` ("nome: motivo") so the caller can log it.
func ExpandWith(ctx context.Context, ranges []protocol.IPRange, lookup LookupFunc) ([]Target, []string, error) {
	seen := map[Target]bool{}
	var out []Target
	var unresolved []string
	for _, r := range ranges {
		var ips []net.IP
		var err error
		if host := strings.TrimSpace(r.Host); host != "" {
			ips, err = resolveHost(ctx, host, lookup)
			if err != nil {
				unresolved = append(unresolved, host+": "+err.Error())
				continue
			}
		} else {
			ips, err = expandRange(r)
		}
		if err != nil {
			return nil, nil, fmt.Errorf("faixa %s: %w", describe(r), err)
		}
		excl, err := exclusions(r.Exclusions)
		if err != nil {
			return nil, nil, fmt.Errorf("faixa %s: %w", describe(r), err)
		}
		ports := r.Ports
		if len(ports) == 0 {
			ports = []int{DefaultPort}
		}
		for _, ip := range ips {
			if excl(ip) {
				continue
			}
			for _, p := range ports {
				if p < 1 || p > 65535 {
					return nil, nil, fmt.Errorf("faixa %s: porta inválida %d", describe(r), p)
				}
				t := Target{IP: ip.String(), Port: p}
				if !seen[t] {
					seen[t] = true
					out = append(out, t)
				}
			}
			if len(out) > MaxTargets {
				return nil, nil, fmt.Errorf("faixas somam mais de %d endereços; divida em faixas menores", MaxTargets)
			}
		}
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := ipToUint(net.ParseIP(out[i].IP)), ipToUint(net.ParseIP(out[j].IP))
		if a != b {
			return a < b
		}
		return out[i].Port < out[j].Port
	})
	return out, unresolved, nil
}

func resolveHost(ctx context.Context, host string, lookup LookupFunc) ([]net.IP, error) {
	if ip := net.ParseIP(host).To4(); ip != nil {
		return []net.IP{ip}, nil
	}
	addrs, err := lookup(ctx, host)
	if err != nil {
		return nil, err
	}
	var out []net.IP
	for _, a := range addrs {
		if ip := net.ParseIP(a).To4(); ip != nil {
			out = append(out, ip)
		}
	}
	if len(out) == 0 {
		return nil, errors.New("o nome não tem endereço IPv4")
	}
	return out, nil
}

func describe(r protocol.IPRange) string {
	switch {
	case r.CIDR != "":
		return r.CIDR
	case r.Host != "":
		return r.Host
	}
	return r.Start + "-" + r.End
}

func ipToUint(ip net.IP) uint32 {
	v4 := ip.To4()
	if v4 == nil {
		return 0
	}
	return binary.BigEndian.Uint32(v4)
}

func uintToIP(n uint32) net.IP {
	b := make([]byte, 4)
	binary.BigEndian.PutUint32(b, n)
	return net.IP(b)
}

func expandRange(r protocol.IPRange) ([]net.IP, error) {
	switch {
	case r.CIDR != "":
		ip, ipnet, err := net.ParseCIDR(strings.TrimSpace(r.CIDR))
		if err != nil || ip.To4() == nil {
			return nil, errors.New("CIDR IPv4 inválido")
		}
		ones, bits := ipnet.Mask.Size()
		if bits-ones > 16 {
			return nil, errors.New("CIDR maior que /16")
		}
		first := ipToUint(ipnet.IP)
		size := uint32(1) << uint(bits-ones) //nolint:gosec // G115: bits-ones <= 16
		start, end := first, first+size-1
		if size > 2 { // sem endereço de rede e de broadcast
			start, end = first+1, first+size-2
		}
		return span(start, end), nil
	case r.Start != "" && r.End != "":
		a, b := net.ParseIP(strings.TrimSpace(r.Start)).To4(), net.ParseIP(strings.TrimSpace(r.End)).To4()
		if a == nil || b == nil {
			return nil, errors.New("início/fim IPv4 inválidos")
		}
		s, e := ipToUint(a), ipToUint(b)
		if s > e {
			return nil, errors.New("início maior que o fim")
		}
		if e-s >= MaxTargets {
			return nil, errors.New("intervalo grande demais")
		}
		return span(s, e), nil
	default:
		return nil, errors.New("faixa sem CIDR nem início/fim")
	}
}

func span(start, end uint32) []net.IP {
	out := make([]net.IP, 0, end-start+1)
	for n := start; ; n++ {
		out = append(out, uintToIP(n))
		if n == end {
			break
		}
	}
	return out
}

func exclusions(list []string) (func(net.IP) bool, error) {
	var nets []*net.IPNet
	var ips []net.IP
	for _, e := range list {
		e = strings.TrimSpace(e)
		if e == "" {
			continue
		}
		if strings.Contains(e, "/") {
			_, n, err := net.ParseCIDR(e)
			if err != nil {
				return nil, fmt.Errorf("exclusão inválida %q", e)
			}
			nets = append(nets, n)
			continue
		}
		if a, b, ok := strings.Cut(e, "-"); ok {
			ra, err := expandRange(protocol.IPRange{Start: a, End: b})
			if err != nil {
				return nil, fmt.Errorf("exclusão inválida %q", e)
			}
			ips = append(ips, ra...)
			continue
		}
		ip := net.ParseIP(e)
		if ip == nil {
			return nil, fmt.Errorf("exclusão inválida %q", e)
		}
		ips = append(ips, ip)
	}
	return func(ip net.IP) bool {
		for _, n := range nets {
			if n.Contains(ip) {
				return true
			}
		}
		for _, x := range ips {
			if x.Equal(ip) {
				return true
			}
		}
		return false
	}, nil
}

// Conn is an SNMP session that can be closed.
type Conn interface {
	snmp.Source
	Close() error
}

// Dialer opens an SNMP session (snmp.Dial in production; fakes in tests).
type Dialer func(host string, port int, cred snmp.Credential, opts snmp.Options) (Conn, error)

// DefaultDialer uses the real SNMP client.
func DefaultDialer(host string, port int, cred snmp.Credential, opts snmp.Options) (Conn, error) {
	return snmp.Dial(host, port, cred, opts)
}

// Found is a printer found by the scan.
type Found struct {
	Target       Target
	CredentialID string
	Probe        printer.Probe
	Elapsed      time.Duration
}

// Scanner probes targets.
type Scanner struct {
	Credentials []snmp.Credential
	Options     snmp.Options
	Concurrency int
	RatePPS     int
	Dial        Dialer
	// Known returns the credential id that worked last time for the target ("" if unknown).
	Known func(Target) string
}

// Scan probes all targets and calls onFound for every printer (from several goroutines, serialized).
// It returns the number of targets probed.
func (s *Scanner) Scan(ctx context.Context, targets []Target, onFound func(Found)) (int, error) {
	if len(s.Credentials) == 0 {
		return 0, errors.New("nenhuma credencial SNMP configurada para o local")
	}
	conc := s.Concurrency
	if conc <= 0 {
		conc = 64
	}
	pps := s.RatePPS
	if pps <= 0 {
		pps = 200
	}
	limiter := rate.NewLimiter(rate.Limit(pps), pps)
	dial := s.Dial
	if dial == nil {
		dial = DefaultDialer
	}
	jobs := make(chan Target)
	var mu sync.Mutex
	var wg sync.WaitGroup
	probed := 0
	for range conc {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for t := range jobs {
				f, ok := s.probe(ctx, dial, limiter, t)
				mu.Lock()
				probed++
				if ok {
					onFound(f)
				}
				mu.Unlock()
			}
		}()
	}
	var err error
feed:
	for _, t := range targets {
		select {
		case <-ctx.Done():
			err = ctx.Err()
			break feed
		case jobs <- t:
		}
	}
	close(jobs)
	wg.Wait()
	return probed, err
}

func (s *Scanner) ordered(t Target) []snmp.Credential {
	known := ""
	if s.Known != nil {
		known = s.Known(t)
	}
	if known == "" {
		return s.Credentials
	}
	out := make([]snmp.Credential, 0, len(s.Credentials))
	for _, c := range s.Credentials {
		if c.ID == known {
			out = append([]snmp.Credential{c}, out...)
		} else {
			out = append(out, c)
		}
	}
	return out
}

func (s *Scanner) probe(ctx context.Context, dial Dialer, limiter *rate.Limiter, t Target) (Found, bool) {
	for _, cred := range s.ordered(t) {
		// Cada tentativa pode gerar até 1 + Retries pacotes.
		if err := limiter.WaitN(ctx, 1+max(0, s.Options.Retries)); err != nil {
			return Found{}, false
		}
		start := time.Now()
		conn, err := dial(t.IP, t.Port, cred, s.Options)
		if err != nil {
			continue
		}
		pr, err := printer.ReadProbe(ctx, conn)
		_ = conn.Close()
		if err != nil {
			continue // sem resposta com esta credencial: tenta a próxima
		}
		if pr.SysObjectID == "" && pr.DeviceType == "" && pr.Serial == "" && pr.LifeCount == nil {
			continue
		}
		if !pr.IsPrinter() {
			return Found{}, false // responde, mas não é impressora
		}
		return Found{Target: t, CredentialID: cred.ID, Probe: pr, Elapsed: time.Since(start)}, true
	}
	return Found{}, false
}
