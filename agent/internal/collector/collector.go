// Package collector schedules discovery and the independent reading loops (PROMPT 4.5/4.6):
// counters, supplies, status (only sent on change + hourly confirmation) and attributes. Only the
// cluster MASTER scans and reads. Every result is written to the local outbox before any send.
package collector

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"slices"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/discovery"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

// Task kinds (independent intervals).
const (
	TaskAttributes = "attributes"
	TaskCounters   = "counters"
	TaskSupplies   = "supplies"
	TaskStatus     = "status"
)

var taskOrder = []string{TaskAttributes, TaskCounters, TaskSupplies, TaskStatus}

// Tunables (PROMPT 4.6).
const (
	TickInterval       = 5 * time.Second
	RetryDelay         = 2 * time.Minute
	MaxAttempts        = 3
	StatusConfirmEvery = time.Hour
	FailureReportEvery = 24 * time.Hour
	ReadConcurrency    = 16
	HealthLoop         = "collector"
)

// DefaultIntervals are used when the server sends zero values.
var DefaultIntervals = protocol.Intervals{
	DiscoveryMinutes: 360, CountersMinutes: 60, SuppliesMinutes: 60, StatusMinutes: 10, AttributesMinutes: 1440,
}

// DefaultDiscovery are the scanner defaults (PROMPT 4.5).
var DefaultDiscovery = protocol.DiscoveryConfig{
	Concurrency: 64, RatePPS: 200, TimeoutMS: 1500, Retries: 1, ReadTimeoutMS: 2000,
}

// MaxRetries caps the SNMP retries per query (1 to 5 attempts, PROMPT 16.10).
const MaxRetries = 4

// Deps are the collector's dependencies.
type Deps struct {
	Store     *store.Store
	Log       *slog.Logger
	Health    *health.Registry
	Dial      discovery.Dialer
	Clock     func() time.Time // relógio corrigido pelo servidor
	Suggest   func(context.Context, []string) error
	LocalIPs  func() []string
	OnEnqueue func()
	Lookup    discovery.LookupFunc // resolve hostnames avulsos (padrão: resolvedor do sistema)
}

type devState struct {
	next         map[string]time.Time
	attempts     int
	retryAt      time.Time
	lastReported time.Time
}

// Collector runs discovery and readings.
type Collector struct {
	d Deps

	mu        sync.Mutex
	cfg       *protocol.AgentConfig
	profiles  []*profile.Profile
	creds     []snmp.Credential
	credByID  map[string]snmp.Credential
	intervals protocol.Intervals
	disc      protocol.DiscoveryConfig
	role      string
	paused    bool
	state     map[string]*devState
	inflight  map[string]bool
	suggested string
	scanKey   string
	ignored   map[string]bool // seriais descartados em Descobertas (16.1)

	scanning atomic.Bool
	scanReq  atomic.Bool
	readReq  atomic.Bool
	lastScan atomic.Int64
	lastRead atomic.Int64

	sem  chan struct{}
	wake chan struct{}
	wg   sync.WaitGroup
}

// New creates a collector (role standby until the server says otherwise).
func New(d Deps) *Collector {
	if d.Dial == nil {
		d.Dial = discovery.DefaultDialer
	}
	if d.Clock == nil {
		d.Clock = time.Now
	}
	if d.LocalIPs == nil {
		d.LocalIPs = osinfo.LocalIPv4
	}
	if d.OnEnqueue == nil {
		d.OnEnqueue = func() {}
	}
	if d.Lookup == nil {
		d.Lookup = discovery.DefaultLookup
	}
	return &Collector{
		d: d, role: "standby", state: map[string]*devState{}, inflight: map[string]bool{},
		sem: make(chan struct{}, ReadConcurrency), wake: make(chan struct{}, 1),
		intervals: DefaultIntervals, disc: DefaultDiscovery, credByID: map[string]snmp.Credential{},
	}
}

func key(ip string, port int) string { return fmt.Sprintf("%s:%d", ip, port) }

// Apply installs a new configuration from the server. Invalid profiles are logged and skipped.
func (c *Collector) Apply(cfg *protocol.AgentConfig) error {
	var profiles []*profile.Profile
	var bad []string
	for _, raw := range cfg.Profiles {
		p, err := profile.FromJSON(raw)
		if err != nil {
			bad = append(bad, err.Error())
			continue
		}
		profiles = append(profiles, p)
	}
	intervals := cfg.Intervals
	fill := func(v *int, def int) {
		if *v <= 0 {
			*v = def
		}
	}
	fill(&intervals.DiscoveryMinutes, DefaultIntervals.DiscoveryMinutes)
	fill(&intervals.CountersMinutes, DefaultIntervals.CountersMinutes)
	fill(&intervals.SuppliesMinutes, DefaultIntervals.SuppliesMinutes)
	fill(&intervals.StatusMinutes, DefaultIntervals.StatusMinutes)
	fill(&intervals.AttributesMinutes, DefaultIntervals.AttributesMinutes)
	disc := cfg.Discovery
	fill(&disc.Concurrency, DefaultDiscovery.Concurrency)
	fill(&disc.RatePPS, DefaultDiscovery.RatePPS)
	fill(&disc.TimeoutMS, DefaultDiscovery.TimeoutMS)
	fill(&disc.ReadTimeoutMS, DefaultDiscovery.ReadTimeoutMS)
	if disc.Retries < 0 {
		disc.Retries = DefaultDiscovery.Retries
	}
	disc.Retries = min(disc.Retries, MaxRetries)
	ignored := map[string]bool{}
	for _, s := range cfg.IgnoredSerials {
		ignored[s] = true
	}
	// Faixas ou credenciais novas pedem varredura imediata: uma impressora que não respondia com a
	// comunidade antiga pode responder com a nova (não dá para esperar o intervalo de descoberta).
	rk, _ := json.Marshal(struct {
		R []protocol.IPRange
		C []snmp.Credential
		L bool
	}{cfg.Ranges, cfg.Credentials, cfg.MonitorLocalNetworks})
	sum := sha256.Sum256(rk)
	c.mu.Lock()
	scanNeeded := hex.EncodeToString(sum[:]) != c.scanKey
	c.cfg, c.profiles, c.creds, c.intervals, c.disc = cfg, profiles, cfg.Credentials, intervals, disc
	c.ignored = ignored
	c.scanKey = hex.EncodeToString(sum[:])
	c.credByID = map[string]snmp.Credential{}
	for _, cr := range cfg.Credentials {
		c.credByID[cr.ID] = cr
	}
	if cfg.ClusterRole != "" {
		c.role = cfg.ClusterRole
	}
	c.paused = cfg.Paused
	c.mu.Unlock()
	if scanNeeded {
		c.scanReq.Store(true)
		c.kick()
	}
	c.forgetIgnored(context.Background())
	if len(bad) > 0 {
		return fmt.Errorf("%d perfil(is) inválido(s) ignorado(s): %s", len(bad), strings.Join(bad, "; "))
	}
	return nil
}

// SetRole updates the cluster role and pause flag (from heartbeats).
func (c *Collector) SetRole(role string, paused bool) {
	c.mu.Lock()
	changed := c.role != role
	c.role, c.paused = role, paused
	c.mu.Unlock()
	if changed {
		c.d.Log.Info("papel no cluster alterado", "papel", role)
		c.kick()
	}
}

// Role returns the current cluster role.
func (c *Collector) Role() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.role
}

// Paused reports whether collection is paused.
func (c *Collector) Paused() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.paused
}

// RequestScan schedules an immediate discovery (command scan_now).
func (c *Collector) RequestScan() { c.scanReq.Store(true); c.kick() }

// RequestRead schedules an immediate read of every device (command read_now).
func (c *Collector) RequestRead() { c.readReq.Store(true); c.kick() }

func (c *Collector) kick() {
	select {
	case c.wake <- struct{}{}:
	default:
	}
}

// LastScan and LastRead return the times of the last completed scan / successful read.
func (c *Collector) LastScan() time.Time { return unixMs(c.lastScan.Load()) }

// LastRead returns the time of the last successful read.
func (c *Collector) LastRead() time.Time { return unixMs(c.lastRead.Load()) }

func unixMs(ms int64) time.Time {
	if ms == 0 {
		return time.Time{}
	}
	return time.UnixMilli(ms).UTC()
}

// Run loops until ctx ends, then waits for in-flight reads.
func (c *Collector) Run(ctx context.Context) {
	c.d.Health.Register(HealthLoop, TickInterval)
	t := time.NewTicker(TickInterval)
	defer t.Stop()
	for {
		c.d.Health.Beat(HealthLoop)
		c.tick(ctx)
		select {
		case <-ctx.Done():
			c.wg.Wait()
			return
		case <-t.C:
		case <-c.wake:
		}
	}
}

func (c *Collector) tick(ctx context.Context) {
	c.mu.Lock()
	cfg, role, paused, intervals := c.cfg, c.role, c.paused, c.intervals
	c.mu.Unlock()
	if cfg == nil || role != "master" || paused {
		return
	}
	now := time.Now()
	scanDue := c.lastScan.Load() == 0 ||
		now.Sub(c.LastScan()) >= time.Duration(intervals.DiscoveryMinutes)*time.Minute || c.scanReq.Load()
	if scanDue && c.scanning.CompareAndSwap(false, true) {
		c.scanReq.Store(false)
		c.wg.Add(1)
		go func() {
			defer c.wg.Done()
			defer c.scanning.Store(false)
			c.scan(ctx)
		}()
	}
	devices, err := c.d.Store.Devices(ctx)
	if err != nil {
		c.d.Log.Error("ler equipamentos conhecidos", "erro", err)
		return
	}
	readAll := c.readReq.Swap(false)
	for _, dev := range devices {
		if c.isIgnored(dev.Serial) {
			continue
		}
		k := key(dev.IP, dev.Port)
		c.mu.Lock()
		if c.inflight[k] {
			c.mu.Unlock()
			continue
		}
		st := c.stateFor(k, now)
		if !st.retryAt.IsZero() && now.Before(st.retryAt) && !readAll {
			c.mu.Unlock()
			continue
		}
		var due []string
		for _, task := range taskOrder {
			if readAll || !now.Before(st.next[task]) {
				due = append(due, task)
			}
		}
		if len(due) == 0 {
			c.mu.Unlock()
			continue
		}
		c.inflight[k] = true
		c.mu.Unlock()
		c.wg.Add(1)
		go func(dev store.Device, due []string) {
			defer c.wg.Done()
			select {
			case c.sem <- struct{}{}:
			case <-ctx.Done():
				c.release(key(dev.IP, dev.Port))
				return
			}
			defer func() { <-c.sem }()
			_ = c.readDevice(ctx, dev, due) // falhas já registradas (log, contagem e evento read_failed)
		}(dev, due)
	}
}

// stateFor must be called with c.mu held. New devices have every task due now.
func (c *Collector) stateFor(k string, now time.Time) *devState {
	st, ok := c.state[k]
	if !ok {
		st = &devState{next: map[string]time.Time{}}
		for _, task := range taskOrder {
			st.next[task] = now
		}
		c.state[k] = st
	}
	return st
}

func (c *Collector) release(k string) {
	c.mu.Lock()
	delete(c.inflight, k)
	c.mu.Unlock()
}

// snmpOptions are the discovery options (timeout of the scan).
func (c *Collector) snmpOptions() snmp.Options {
	c.mu.Lock()
	defer c.mu.Unlock()
	o := snmp.DefaultOptions()
	o.Timeout = time.Duration(c.disc.TimeoutMS) * time.Millisecond
	o.Retries = c.disc.Retries
	return o
}

// readOptions are the options of the readings (their own timeout, PROMPT 16.10).
func (c *Collector) readOptions() snmp.Options {
	c.mu.Lock()
	defer c.mu.Unlock()
	o := snmp.DefaultOptions()
	o.Timeout = time.Duration(c.disc.ReadTimeoutMS) * time.Millisecond
	o.Retries = c.disc.Retries
	return o
}

// isIgnored reports whether the serial was discarded in Descobertas.
func (c *Collector) isIgnored(serial string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return serial != "" && c.ignored[serial]
}

// forgetIgnored drops discarded devices from the local store: they are no longer read.
func (c *Collector) forgetIgnored(ctx context.Context) {
	devices, err := c.d.Store.Devices(ctx)
	if err != nil {
		c.d.Log.Error("ler equipamentos conhecidos", "erro", err)
		return
	}
	for _, d := range devices {
		if c.isIgnored(d.Serial) {
			if err := c.d.Store.RemoveDevice(ctx, d.IP, d.Port); err != nil {
				c.d.Log.Error("remover equipamento descartado", "ip", d.IP, "erro", err)
				continue
			}
			c.d.Log.Info("equipamento descartado no portal deixa de ser lido", "ip", d.IP, "serial", d.Serial)
		}
	}
}

func (c *Collector) credential(id string) (snmp.Credential, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if cr, ok := c.credByID[id]; ok {
		return cr, true
	}
	if len(c.creds) > 0 {
		return c.creds[0], true
	}
	return snmp.Credential{}, false
}

func (c *Collector) profilesSnapshot() []*profile.Profile {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.profiles
}

func (c *Collector) profileByKey(k string) *profile.Profile {
	for _, p := range c.profilesSnapshot() {
		if p.ID == k {
			return p
		}
	}
	return nil
}

// errTransport marks failures where the device did not answer.
var errTransport = errors.New("equipamento sem resposta")

func (c *Collector) readDevice(ctx context.Context, dev store.Device, due []string) error {
	k := key(dev.IP, dev.Port)
	defer c.release(k)
	err := c.readTasks(ctx, &dev, due)
	now := time.Now()
	c.mu.Lock()
	st := c.stateFor(k, now)
	c.mu.Unlock()
	if err == nil {
		if e := c.d.Store.MarkOK(ctx, dev.IP, dev.Port); e != nil {
			c.d.Log.Error("registrar leitura ok", "ip", dev.IP, "erro", e)
		}
		c.mu.Lock()
		st.attempts, st.retryAt = 0, time.Time{}
		for _, task := range due {
			st.next[task] = now.Add(c.intervalLocked(task))
		}
		c.mu.Unlock()
		c.lastRead.Store(now.UnixMilli())
		return nil
	}
	if ctx.Err() != nil {
		return err
	}
	if !errors.Is(err, errTransport) {
		c.d.Log.Error("falha ao ler equipamento", "ip", dev.IP, "porta", dev.Port, "erro", err)
	}
	failures, ferr := c.d.Store.MarkFailure(ctx, dev.IP, dev.Port)
	if ferr != nil {
		c.d.Log.Error("registrar falha", "ip", dev.IP, "erro", ferr)
	}
	c.mu.Lock()
	st.attempts++
	attempts := st.attempts
	if attempts < MaxAttempts {
		// Impressora em economia de energia costuma responder na tentativa seguinte (PROMPT 4.6).
		st.retryAt = now.Add(RetryDelay)
		c.mu.Unlock()
		c.d.Log.Warn("equipamento não respondeu; nova tentativa em 2 min", "ip", dev.IP, "tentativa", attempts)
		return err
	}
	report := st.lastReported.IsZero() || now.Sub(st.lastReported) >= FailureReportEvery
	if report {
		st.lastReported = now
	}
	st.attempts, st.retryAt = 0, time.Time{}
	for _, task := range due {
		st.next[task] = now.Add(c.intervalLocked(task))
	}
	c.mu.Unlock()
	c.d.Log.Warn("equipamento sem resposta após 3 tentativas", "ip", dev.IP, "falhas_seguidas", failures)
	if report {
		ref := refFromStore(dev)
		ev := &protocol.EventPayload{Type: "read_failed", Data: map[string]any{
			"attempts": MaxAttempts, "error": err.Error(), "consecutive_failures": failures,
		}}
		c.enqueue(ctx, protocol.Item{Kind: protocol.KindEvent, Device: ref, Event: ev})
	}
	return err
}

func (c *Collector) intervalLocked(task string) time.Duration {
	m := map[string]int{
		TaskAttributes: c.intervals.AttributesMinutes, TaskCounters: c.intervals.CountersMinutes,
		TaskSupplies: c.intervals.SuppliesMinutes, TaskStatus: c.intervals.StatusMinutes,
	}[task]
	return time.Duration(m) * time.Minute
}

func refFromStore(dev store.Device) protocol.DeviceRef {
	ref := protocol.DeviceRef{IP: dev.IP, Port: dev.Port, Serial: dev.Serial, SysObjectID: dev.SysObjectID,
		Model: dev.Model, ProfileKey: dev.ProfileKey}
	var id printer.Identity
	if json.Unmarshal([]byte(dev.Identity), &id) == nil && id.Serial != "" {
		ref.MAC, ref.Hostname, ref.SysDescr, ref.Firmware = id.MAC, id.SysName, id.SysDescr, id.Firmware
		ref.SysLocation = id.SysLocation
	}
	return ref
}

func refFromIdentity(ip string, port int, id printer.Identity) protocol.DeviceRef {
	return protocol.DeviceRef{
		IP: ip, Port: port, Serial: id.Serial, MAC: id.MAC, Hostname: id.SysName, SysObjectID: id.SysObjectID,
		SysDescr: id.SysDescr, Model: id.Model, Firmware: id.Firmware, ProfileKey: id.ProfileKey,
		SysLocation: id.SysLocation,
	}
}

// fallbackSerial gives devices without an SNMP serial a stable identity based on the MAC address.
func fallbackSerial(id *printer.Identity) bool {
	if id.Serial != "" {
		return true
	}
	if id.MAC != "" {
		id.Serial = "MAC-" + strings.ReplaceAll(id.MAC, ":", "")
		return true
	}
	return false
}

func (c *Collector) readTasks(ctx context.Context, dev *store.Device, due []string) error {
	cred, ok := c.credential(dev.CredentialID)
	if !ok {
		return errors.New("nenhuma credencial SNMP configurada")
	}
	conn, err := c.d.Dial(dev.IP, dev.Port, cred, c.readOptions())
	if err != nil {
		return err
	}
	defer func() { _ = conn.Close() }()
	wrap := func(err error) error {
		if errors.Is(err, snmp.ErrTimeout) {
			return fmt.Errorf("%w: %s", errTransport, err.Error())
		}
		return err
	}
	ref := refFromStore(*dev)
	p := c.profileByKey(dev.ProfileKey)
	refreshIdentity := func() error {
		id, np, err := printer.ReadIdentity(ctx, conn, c.profilesSnapshot())
		if err != nil {
			return wrap(err)
		}
		if !fallbackSerial(&id) {
			return errors.New("equipamento sem número de série nem MAC: não é possível identificá-lo")
		}
		raw, _ := json.Marshal(id)
		dev.Serial, dev.SysObjectID, dev.Model, dev.Identity = id.Serial, id.SysObjectID, id.Model, string(raw)
		if np != nil {
			dev.ProfileKey, p = np.ID, np
		}
		ref = refFromIdentity(dev.IP, dev.Port, id)
		return c.d.Store.UpsertDevice(ctx, *dev)
	}
	for _, task := range due {
		switch task {
		case TaskAttributes:
			if err := refreshIdentity(); err != nil {
				return err
			}
			var id printer.Identity
			_ = json.Unmarshal([]byte(dev.Identity), &id)
			attrs, err := printer.ReadAttributes(ctx, conn, p, id)
			if err != nil {
				return wrap(err)
			}
			c.enqueue(ctx, protocol.Item{Kind: protocol.KindAttributes, Device: ref, Attributes: &attrs})
		case TaskCounters:
			// Identidade por serial: se o IP passou a responder com outro serial, relê tudo (PROMPT 4.6).
			pid, err := profile.ResolveIdentity(ctx, conn, p)
			if err != nil {
				return wrap(err)
			}
			if p == nil || (pid.Serial != "" && pid.Serial != dev.Serial) {
				if err := refreshIdentity(); err != nil {
					return err
				}
			}
			res, err := profile.Evaluate(ctx, conn, p, ref.Model)
			if err != nil {
				return wrap(err)
			}
			st, err := printer.ReadStatus(ctx, conn, p)
			if err != nil {
				return wrap(err)
			}
			c.enqueue(ctx, protocol.Item{Kind: protocol.KindReading, Device: ref, Reading: &protocol.ReadingPayload{
				Counters: res.Counters, CounterLines: res.Lines, Extra: res.Extra, CounterSource: res.Source,
				ProfileKey:     res.ProfileID,
				ProfileVersion: res.ProfileVersion, MonoOnly: res.MonoOnly, SumTolerancePercent: res.SumTolerancePercent,
				Unresolved: res.Unresolved, Status: st.Status, ErrorBits: st.ErrorBits, Source: "snmp",
			}})
		case TaskSupplies:
			if p != nil && !p.UseStandardSupplies() {
				continue
			}
			sup, err := printer.ReadSupplies(ctx, conn)
			if err != nil {
				return wrap(err)
			}
			if p != nil && p.Supplies != nil && usable(p.Supplies.CartridgeSerialOID) && len(sup) > 0 {
				if err := printer.AttachCartridgeSerials(ctx, conn, p.Supplies.CartridgeSerialOID, sup); err != nil {
					return wrap(err)
				}
			}
			if len(sup) > 0 {
				c.enqueue(ctx, protocol.Item{Kind: protocol.KindSupplies, Device: ref, Supplies: sup})
			}
		case TaskStatus:
			st, err := printer.ReadStatus(ctx, conn, p)
			if err != nil {
				return wrap(err)
			}
			hash := statusHash(st)
			if hash != dev.StatusHash || time.Since(dev.StatusSent) >= StatusConfirmEvery {
				c.enqueue(ctx, protocol.Item{Kind: protocol.KindStatus, Device: ref, Status: &st})
				if err := c.d.Store.SetStatusSent(ctx, dev.IP, dev.Port, hash, time.Now()); err != nil {
					return err
				}
				dev.StatusHash, dev.StatusSent = hash, time.Now()
			}
		}
	}
	return nil
}

func statusHash(st printer.StatusResult) string {
	raw, _ := json.Marshal(struct {
		S string
		B int
		R []string
		P string
		A []printer.Alert
	}{st.Status, st.ErrorBits, st.Reasons, st.PanelText, st.Alerts})
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func (c *Collector) enqueue(ctx context.Context, it protocol.Item) {
	it.ReadAt = c.d.Clock().UTC()
	raw, err := json.Marshal(it)
	if err != nil {
		c.d.Log.Error("serializar item", "erro", err)
		return
	}
	if _, err := c.d.Store.Enqueue(ctx, it.Kind, raw, it.ReadAt); err != nil {
		// Falha grave: a leitura não pôde nem ser guardada localmente.
		c.d.Log.Error("NÃO foi possível gravar na fila local", "tipo", it.Kind, "serial", it.Device.Serial, "erro", err)
		return
	}
	c.d.OnEnqueue()
}

// ScanResult summarizes a discovery run.
type ScanResult struct {
	Ranges    int     `json:"ranges"`
	Targets   int     `json:"targets"`
	Probed    int     `json:"probed"`
	Found     int     `json:"printers_found"`
	New       int     `json:"new"`
	Removed   int     `json:"removed"`
	DurationS float64 `json:"duration_s"`
}

// scan is the periodic discovery (all approved ranges; suggestion when there is none).
func (c *Collector) scan(ctx context.Context) {
	defer func() { c.lastScan.Store(time.Now().UnixMilli()) }()
	c.mu.Lock()
	cfg := c.cfg
	c.mu.Unlock()
	ranges := cfg.Ranges
	if cfg.MonitorLocalNetworks {
		// "Monitorar redes conectadas" (16.10): as /24 privadas das interfaces, acompanhando o PC.
		ranges = append(append([]protocol.IPRange{}, ranges...), c.localRanges()...)
	}
	if len(ranges) == 0 {
		c.suggestRanges(ctx)
		return
	}
	_, _ = c.scanRanges(ctx, ranges, true) // erros já vão para o log
}

// localRanges are the private /24 networks of this PC's interfaces, on the default SNMP port.
func (c *Collector) localRanges() []protocol.IPRange {
	subnets := osinfo.PrivateSubnets24(c.d.LocalIPs())
	sort.Strings(subnets)
	out := make([]protocol.IPRange, 0, len(subnets))
	for _, s := range subnets {
		out = append(out, protocol.IPRange{ID: "local:" + s, CIDR: s, Ports: []int{discovery.DefaultPort}})
	}
	return out
}

// scanRanges probes the given ranges and registers the printers found. full=true means these are
// all the site's ranges, so known devices outside them stop being read by this agent.
func (c *Collector) scanRanges(ctx context.Context, ranges []protocol.IPRange, full bool) (ScanResult, error) {
	start := time.Now()
	res := ScanResult{Ranges: len(ranges)}
	c.mu.Lock()
	creds, disc := c.creds, c.disc
	c.mu.Unlock()
	targets, unresolved, err := discovery.ExpandWith(ctx, ranges, c.d.Lookup)
	if err != nil {
		c.d.Log.Error("faixas de IP inválidas", "erro", err)
		return res, fmt.Errorf("faixas de IP inválidas: %w", err)
	}
	for _, u := range unresolved {
		c.d.Log.Error("hostname avulso não resolvido (fica fora desta varredura)", "detalhe", u)
	}
	res.Targets = len(targets)
	known := map[string]string{}
	devices, err := c.d.Store.Devices(ctx)
	if err != nil {
		c.d.Log.Error("ler equipamentos conhecidos", "erro", err)
		return res, err
	}
	for _, d := range devices {
		known[key(d.IP, d.Port)] = d.CredentialID
	}
	sc := &discovery.Scanner{
		Credentials: creds, Concurrency: disc.Concurrency, RatePPS: disc.RatePPS, Dial: c.d.Dial,
		Options: c.snmpOptions(),
		Known:   func(t discovery.Target) string { return known[key(t.IP, t.Port)] },
	}
	c.d.Log.Info("varredura iniciada", "alvos", len(targets))
	var found []discovery.Found
	probed, err := sc.Scan(ctx, targets, func(f discovery.Found) { found = append(found, f) })
	res.Probed = probed
	if err != nil {
		c.d.Log.Error("varredura interrompida", "erro", err, "sondados", probed)
		return res, err
	}
	res.Found = len(found)
	for _, f := range found {
		if isNew, err := c.register(ctx, f); err != nil {
			c.d.Log.Error("registrar impressora encontrada", "ip", f.Target.IP, "porta", f.Target.Port, "erro", err)
		} else if isNew {
			res.New++
		}
	}
	if full {
		// Equipamentos cujo IP saiu das faixas aprovadas deixam de ser lidos por este coletor.
		inRange := map[string]bool{}
		for _, t := range targets {
			inRange[key(t.IP, t.Port)] = true
		}
		for _, d := range devices {
			if !inRange[key(d.IP, d.Port)] {
				if err := c.d.Store.RemoveDevice(ctx, d.IP, d.Port); err != nil {
					c.d.Log.Error("remover equipamento fora das faixas", "ip", d.IP, "erro", err)
				} else {
					res.Removed++
				}
			}
		}
	}
	res.DurationS = time.Since(start).Seconds()
	c.d.Log.Info("varredura concluída", "sondados", probed, "impressoras", res.Found, "novas", res.New,
		"duracao_s", res.DurationS)
	c.kick()
	return res, nil
}

func (c *Collector) register(ctx context.Context, f discovery.Found) (bool, error) {
	cred, ok := c.credential(f.CredentialID)
	if !ok {
		return false, errors.New("credencial sumiu da configuração")
	}
	conn, err := c.d.Dial(f.Target.IP, f.Target.Port, cred, c.snmpOptions())
	if err != nil {
		return false, err
	}
	defer func() { _ = conn.Close() }()
	id, p, err := printer.ReadIdentity(ctx, conn, c.profilesSnapshot())
	if err != nil {
		return false, err
	}
	if !fallbackSerial(&id) {
		return false, errors.New("impressora sem número de série nem MAC")
	}
	if c.isIgnored(id.Serial) {
		return false, nil // descartada em Descobertas: não entra na lista de leitura
	}
	existing, err := c.d.Store.DeviceAt(ctx, f.Target.IP, f.Target.Port)
	if err != nil {
		return false, err
	}
	raw, _ := json.Marshal(id)
	dev := store.Device{
		IP: f.Target.IP, Port: f.Target.Port, Serial: id.Serial, SysObjectID: id.SysObjectID, Model: id.Model,
		CredentialID: f.CredentialID, Identity: string(raw),
	}
	if p != nil {
		dev.ProfileKey = p.ID
	}
	if err := c.d.Store.UpsertDevice(ctx, dev); err != nil {
		return false, err
	}
	isNew := existing == nil || existing.Serial != id.Serial
	if isNew {
		c.d.Log.Info("impressora encontrada", "ip", f.Target.IP, "porta", f.Target.Port, "serial", id.Serial,
			"modelo", id.Model, "perfil", dev.ProfileKey)
		c.mu.Lock()
		delete(c.state, key(f.Target.IP, f.Target.Port)) // tudo vence agora: leitura imediata
		c.mu.Unlock()
	}
	return isNew, nil
}

func (c *Collector) suggestRanges(ctx context.Context) {
	subnets := osinfo.PrivateSubnets24(c.d.LocalIPs())
	if len(subnets) == 0 {
		c.d.Log.Warn("local sem faixa de IP aprovada e nenhuma rede privada encontrada neste PC")
		return
	}
	sort.Strings(subnets)
	k := strings.Join(subnets, ",")
	c.mu.Lock()
	already := c.suggested == k
	c.mu.Unlock()
	if already || c.d.Suggest == nil {
		return
	}
	if err := c.d.Suggest(ctx, subnets); err != nil {
		c.d.Log.Error("enviar sugestão de faixas ao portal", "erro", err)
		return
	}
	c.mu.Lock()
	c.suggested = k
	c.mu.Unlock()
	c.d.Log.Info("sem faixa aprovada: sub-redes sugeridas ao portal (nada será varrido até a aprovação)", "faixas", subnets)
}

// KnownDevices returns how many devices the agent knows (for heartbeats).
func (c *Collector) KnownDevices(ctx context.Context) int {
	d, err := c.d.Store.Devices(ctx)
	if err != nil {
		return 0
	}
	return len(d)
}

// Profiles returns the loaded profile ids (diagnostics).
func (c *Collector) Profiles() []string {
	var ids []string
	for _, p := range c.profilesSnapshot() {
		ids = append(ids, p.ID)
	}
	return slices.Sorted(slices.Values(ids))
}

// usable reports whether a profile OID is filled (placeholders are never queried).
func usable(oid string) bool { return oid != "" && oid != profile.Placeholder }
