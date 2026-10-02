package collector

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/discovery"
	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// On-demand operations used by remote commands (PROMPT 4.7): scan_now, read_now, read_device,
// snmp_test and mib_walk.

// Errors returned when an operation needs the collecting role.
var (
	ErrNotMaster = errors.New("este coletor está em espera (STANDBY): só o MASTER do local varre e lê")
	ErrPaused    = errors.New("as coletas deste coletor estão pausadas no portal")
	ErrNoConfig  = errors.New("o coletor ainda não recebeu a configuração do local")
)

func (c *Collector) requireCollecting() (*protocol.AgentConfig, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	switch {
	case c.cfg == nil:
		return nil, ErrNoConfig
	case c.role != "master":
		return nil, ErrNotMaster
	case c.paused:
		return nil, ErrPaused
	}
	return c.cfg, nil
}

// ScanNow runs a discovery immediately (all ranges, or only rangeID) and waits for it.
func (c *Collector) ScanNow(ctx context.Context, rangeID string) (ScanResult, error) {
	cfg, err := c.requireCollecting()
	if err != nil {
		return ScanResult{}, err
	}
	if len(cfg.Ranges) == 0 {
		return ScanResult{}, errors.New("o local não tem faixa de IP aprovada; aprove uma faixa no portal")
	}
	ranges := cfg.Ranges
	if rangeID != "" {
		ranges = nil
		for _, r := range cfg.Ranges {
			if r.ID == rangeID {
				ranges = append(ranges, r)
			}
		}
		if len(ranges) == 0 {
			return ScanResult{}, fmt.Errorf("a faixa %s não está na configuração atual do coletor (aplique a configuração)", rangeID)
		}
	}
	// Uma varredura por vez: espera a periódica terminar, se estiver rodando.
	for !c.scanning.CompareAndSwap(false, true) {
		select {
		case <-ctx.Done():
			return ScanResult{}, ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
	defer c.scanning.Store(false)
	res, err := c.scanRanges(ctx, ranges, rangeID == "")
	if err == nil && rangeID == "" {
		c.lastScan.Store(time.Now().UnixMilli())
	}
	return res, err
}

// Selector picks a device for ReadNow (by serial, or by ip:port).
type Selector struct {
	Serial string `json:"serial"`
	IP     string `json:"ip"`
	Port   int    `json:"port"`
}

// DeviceReadResult is the outcome of one on-demand read.
type DeviceReadResult struct {
	IP     string `json:"ip"`
	Port   int    `json:"port"`
	Serial string `json:"serial"`
	OK     bool   `json:"ok"`
	Error  string `json:"error,omitempty"`
}

// ReadSummary is the result of ReadNow.
type ReadSummary struct {
	Requested int                `json:"requested"`
	OK        int                `json:"ok"`
	Failed    int                `json:"failed"`
	Devices   []DeviceReadResult `json:"devices"`
	NotFound  []Selector         `json:"not_found,omitempty"`
}

// ReadNow reads (every task) all known devices, or the selected ones, and waits for the results.
// Readings go to the outbox like scheduled ones.
func (c *Collector) ReadNow(ctx context.Context, sel []Selector) (ReadSummary, error) {
	if _, err := c.requireCollecting(); err != nil {
		return ReadSummary{}, err
	}
	devices, err := c.d.Store.Devices(ctx)
	if err != nil {
		return ReadSummary{}, err
	}
	var sum ReadSummary
	if len(sel) > 0 {
		var picked []int
		for _, s := range sel {
			idx := -1
			for i, d := range devices {
				if (s.Serial != "" && d.Serial == s.Serial) || (s.IP != "" && d.IP == s.IP && (s.Port == 0 || d.Port == s.Port)) {
					idx = i
					break
				}
			}
			if idx < 0 {
				sum.NotFound = append(sum.NotFound, s)
				continue
			}
			picked = append(picked, idx)
		}
		chosen := devices[:0:0]
		for _, i := range picked {
			chosen = append(chosen, devices[i])
		}
		devices = chosen
	}
	sum.Requested = len(devices) + len(sum.NotFound)
	results := make([]DeviceReadResult, len(devices))
	var wg sync.WaitGroup
	for i, dev := range devices {
		results[i] = DeviceReadResult{IP: dev.IP, Port: dev.Port, Serial: dev.Serial}
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			k := key(devices[i].IP, devices[i].Port)
			// Uma leitura agendada do mesmo equipamento pode estar em curso: espera ela terminar e lê de novo.
			if err := c.claim(ctx, k); err != nil {
				results[i].Error = err.Error()
				return
			}
			select {
			case c.sem <- struct{}{}:
			case <-ctx.Done():
				c.release(k)
				results[i].Error = ctx.Err().Error()
				return
			}
			defer func() { <-c.sem }()
			if err := c.readDevice(ctx, devices[i], taskOrder); err != nil {
				results[i].Error = err.Error()
				return
			}
			results[i].OK = true
		}(i)
	}
	wg.Wait()
	for _, r := range results {
		if r.OK {
			sum.OK++
		} else {
			sum.Failed++
		}
	}
	sum.Failed += len(sum.NotFound)
	sum.Devices = results
	return sum, nil
}

// claim marks the device as being read, waiting while another read of it is in flight.
func (c *Collector) claim(ctx context.Context, k string) error {
	for {
		c.mu.Lock()
		if !c.inflight[k] {
			c.inflight[k] = true
			c.mu.Unlock()
			return nil
		}
		c.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(100 * time.Millisecond):
		}
	}
}

// connectAny opens a session that answers: the credential already known for the address first, then
// each credential of the site in order.
func (c *Collector) connectAny(ctx context.Context, ip string, port int) (discovery.Conn, snmp.Credential, error) {
	c.mu.Lock()
	creds := append([]snmp.Credential(nil), c.creds...)
	c.mu.Unlock()
	if len(creds) == 0 {
		return nil, snmp.Credential{}, errors.New("nenhuma credencial SNMP configurada para o local")
	}
	if dev, err := c.d.Store.DeviceAt(ctx, ip, port); err == nil && dev != nil && dev.CredentialID != "" {
		for i, cr := range creds {
			if cr.ID == dev.CredentialID {
				creds[0], creds[i] = creds[i], creds[0]
				break
			}
		}
	}
	var last error
	for _, cr := range creds {
		conn, err := c.d.Dial(ip, port, cr, c.snmpOptions())
		if err != nil {
			last = err
			continue
		}
		vals, err := conn.Get(ctx, []string{printer.OIDSysObjectID})
		if err == nil && len(vals) == 1 && vals[0].Exists() {
			return conn, cr, nil
		}
		_ = conn.Close()
		if err == nil {
			err = errors.New("sem sysObjectID")
		}
		last = err
	}
	return nil, snmp.Credential{}, fmt.Errorf("nenhuma credencial do local respondeu em %s:%d (%w)", ip, port, last)
}

// ReadRaw performs a full read of one address and returns everything read, without queuing it
// (command read_device: "devolve o resultado bruto na tela"). A non-nil `draft` replaces the selected
// profile (testing a profile in the portal before publishing it).
func (c *Collector) ReadRaw(ctx context.Context, ip string, port int, draft *profile.Profile) (map[string]any, error) {
	start := time.Now()
	conn, cred, err := c.connectAny(ctx, ip, port)
	if err != nil {
		return nil, err
	}
	defer func() { _ = conn.Close() }()
	id, p, err := printer.ReadIdentity(ctx, conn, c.profilesSnapshot())
	if err != nil {
		return nil, fmt.Errorf("ler identificação: %w", err)
	}
	out := map[string]any{"ip": ip, "port": port, "credential_id": cred.ID, "snmp_version": cred.Version, "identity": id}
	if draft != nil {
		p = draft
		out["profile_draft"] = true
	}
	if p != nil {
		out["profile"] = p.ID
		res, err := profile.Evaluate(ctx, conn, p, id.Model)
		if err != nil {
			out["counters_error"] = err.Error()
		} else {
			out["counters"], out["counter_source"], out["extra"] = res.Counters, res.Source, res.Extra
			out["unresolved"], out["mono_only"] = res.Unresolved, res.MonoOnly
		}
	}
	if st, err := printer.ReadStatus(ctx, conn, p); err != nil {
		out["status_error"] = err.Error()
	} else {
		out["status"] = st
	}
	if sup, err := printer.ReadSupplies(ctx, conn); err != nil {
		out["supplies_error"] = err.Error()
	} else {
		out["supplies"] = sup
	}
	out["elapsed_ms"] = time.Since(start).Milliseconds()
	return out, nil
}

// CredentialTest is the outcome of one credential in SNMPTest (never includes secrets).
type CredentialTest struct {
	CredentialID string `json:"credential_id"`
	Version      string `json:"version"`
	OK           bool   `json:"ok"`
	ElapsedMS    int64  `json:"elapsed_ms"`
	SysObjectID  string `json:"sys_object_id,omitempty"`
	SysDescr     string `json:"sys_descr,omitempty"`
	IsPrinter    bool   `json:"is_printer"`
	Serial       string `json:"serial,omitempty"`
	Error        string `json:"error,omitempty"`
}

// SNMPTest tries every credential of the site against one address and reports which answers and how
// fast (command snmp_test).
func (c *Collector) SNMPTest(ctx context.Context, ip string, port int) ([]CredentialTest, error) {
	c.mu.Lock()
	creds := append([]snmp.Credential(nil), c.creds...)
	c.mu.Unlock()
	if len(creds) == 0 {
		return nil, errors.New("nenhuma credencial SNMP configurada para o local")
	}
	out := make([]CredentialTest, 0, len(creds))
	for _, cr := range creds {
		t := CredentialTest{CredentialID: cr.ID, Version: cr.Version}
		start := time.Now()
		conn, err := c.d.Dial(ip, port, cr, c.snmpOptions())
		if err == nil {
			var probe printer.Probe
			probe, err = printer.ReadProbe(ctx, conn)
			if err == nil && probe.SysObjectID == "" {
				err = errors.New("sem resposta útil (sysObjectID ausente)")
			}
			if err == nil {
				t.OK, t.SysObjectID, t.IsPrinter, t.Serial = true, probe.SysObjectID, probe.IsPrinter(), probe.Serial
				if v, gerr := conn.Get(ctx, []string{printer.OIDSysDescr}); gerr == nil && len(v) == 1 {
					t.SysDescr = v[0].String()
				}
			}
			_ = conn.Close()
		}
		t.ElapsedMS = time.Since(start).Milliseconds()
		if err != nil {
			t.Error = err.Error()
		}
		out = append(out, t)
		if ctx.Err() != nil {
			return out, ctx.Err()
		}
	}
	return out, nil
}

// MaxWalkOIDs bounds a walk (a printer has a few thousand OIDs; this protects the agent's memory).
const MaxWalkOIDs = 500_000

// Walk walks root (default 1.3.6.1) on one address and returns the PDUs (command mib_walk).
func (c *Collector) Walk(ctx context.Context, ip string, port int, root string, progress func(int)) ([]snmp.PDU, string, error) {
	if root == "" {
		root = "1.3.6.1"
	}
	conn, cred, err := c.connectAny(ctx, ip, port)
	if err != nil {
		return nil, "", err
	}
	defer func() { _ = conn.Close() }()
	var pdus []snmp.PDU
	err = conn.Walk(ctx, root, func(p snmp.PDU) error {
		pdus = append(pdus, p)
		if len(pdus) >= MaxWalkOIDs {
			return fmt.Errorf("walk passou de %d OIDs; use uma subárvore", MaxWalkOIDs)
		}
		if progress != nil && len(pdus)%500 == 0 {
			progress(len(pdus))
		}
		return nil
	})
	if err != nil {
		return nil, cred.ID, err
	}
	if len(pdus) == 0 {
		return nil, cred.ID, fmt.Errorf("nenhum OID retornado sob %s", root)
	}
	return pdus, cred.ID, nil
}
