// Package agent wires the collector, the outbox uploader, the heartbeat/config loop and the local
// health endpoint into the dm-agent process.
package agent

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"runtime"
	"sync"
	"sync/atomic"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/collector"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/sdnotify"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
	"github.com/daticopy/dati-monitor/agent/internal/store"
	"github.com/daticopy/dati-monitor/agent/internal/uploader"
)

// HeartbeatInterval between heartbeats (PROMPT 4.3).
const HeartbeatInterval = 30 * time.Second

// HealthLoop is the heartbeat loop name in /health.
const HealthLoop = "heartbeat"

// Agent is a running dm-agent.
type Agent struct {
	Dir       string
	Local     *config.Local
	Log       *slog.Logger
	Client    *api.Client
	Store     *store.Store
	Collector *collector.Collector
	Uploader  *uploader.Uploader
	Health    *health.Registry

	meter          osinfo.ProcessMeter
	started        time.Time
	applied        atomic.Int64
	lastHeartbeat  atomic.Int64
	heartbeatError atomic.Value
	mu             sync.Mutex
}

// New loads the local configuration and credential and prepares every component.
func New(dir string, log *slog.Logger) (*Agent, error) {
	local, err := config.Load(dir)
	if err != nil {
		return nil, err
	}
	sec, err := secret.Load(dir)
	if err != nil {
		return nil, err
	}
	client, err := api.New(api.Options{ServerURL: local.ServerURL, InsecureDev: local.InsecureDev, ProxyURL: local.ProxyURL},
		local.AgentID, secret.DeriveKey(sec))
	if err != nil {
		return nil, err
	}
	st, err := store.Open(dir + "/agent.db")
	if err != nil {
		return nil, err
	}
	a := &Agent{Dir: dir, Local: local, Log: log, Client: client, Store: st, Health: health.NewRegistry(), started: time.Now()}
	a.heartbeatError.Store("")
	a.Uploader = &uploader.Uploader{Store: st, Send: client, AgentID: local.AgentID, Log: log, Health: a.Health}
	a.Collector = collector.New(collector.Deps{
		Store: st, Log: log, Health: a.Health, Clock: client.ServerNow,
		Suggest: client.SuggestRanges, OnEnqueue: a.Uploader.Kick,
	})
	a.Health.SetInfo(a.info)
	return a, nil
}

func (a *Agent) info() map[string]any {
	pending, _ := a.Store.Count(context.Background())
	dead, _ := a.Store.DeadCount(context.Background())
	hbErr, _ := a.heartbeatError.Load().(string)
	return map[string]any{
		"version":              buildinfo.Version,
		"agent_id":             a.Local.AgentID,
		"cluster_role":         a.Collector.Role(),
		"paused":               a.Collector.Paused(),
		"queue_pending":        pending,
		"queue_dead":           dead,
		"queue_dropped":        a.Uploader.Dropped(),
		"last_heartbeat_at":    timeOrNil(unixMs(a.lastHeartbeat.Load())),
		"last_heartbeat_error": hbErr,
		"last_upload_at":       timeOrNil(a.Uploader.LastSuccess()),
		"last_upload_error":    a.Uploader.LastError(),
		"last_scan_at":         timeOrNil(a.Collector.LastScan()),
		"last_read_at":         timeOrNil(a.Collector.LastRead()),
		"applied_config":       a.applied.Load(),
		"clock_offset_seconds": a.Client.ClockOffset().Seconds(),
		"profiles":             a.Collector.Profiles(),
	}
}

func unixMs(ms int64) time.Time {
	if ms == 0 {
		return time.Time{}
	}
	return time.UnixMilli(ms).UTC()
}

func timeOrNil(t time.Time) any {
	if t.IsZero() {
		return nil
	}
	return t.UTC()
}

// Run runs until ctx is cancelled.
func (a *Agent) Run(ctx context.Context) error {
	if err := osinfo.CheckSupported(); err != nil {
		return err
	}
	defer func() { _ = a.Store.Close() }()
	a.Log.Info("coletor iniciando", "versao", buildinfo.Version, "agent_id", a.Local.AgentID, "servidor", a.Local.ServerURL,
		"os", osinfo.Describe())
	if a.Local.Server != nil {
		// Configuração em cache: o coletor trabalha mesmo que o servidor esteja fora do ar.
		a.apply(a.Local.Server, false)
	}
	addr := a.Local.HealthAddr
	if addr == "" {
		addr = health.DefaultAddr
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var wg sync.WaitGroup
	errs := make(chan error, 1)
	wg.Add(3)
	go func() {
		defer wg.Done()
		if err := health.Serve(ctx, addr, a.Health); err != nil {
			a.Log.Error("endpoint de saúde não pôde iniciar", "endereco", addr, "erro", err)
			select {
			case errs <- fmt.Errorf("endpoint de saúde em %s: %w", addr, err):
			default:
			}
			cancel()
		}
	}()
	go func() { defer wg.Done(); a.Collector.Run(ctx) }()
	go func() { defer wg.Done(); a.Uploader.Run(ctx) }()
	_ = sdnotify.Ready()
	a.heartbeatLoop(ctx)
	_ = sdnotify.Stopping()
	cancel()
	wg.Wait()
	_ = osinfo.KeepAwake(false)
	a.Log.Info("coletor encerrado")
	select {
	case err := <-errs:
		return err
	default:
		return nil
	}
}

func (a *Agent) heartbeatLoop(ctx context.Context) {
	a.Health.Register(HealthLoop, HeartbeatInterval)
	t := time.NewTicker(HeartbeatInterval)
	defer t.Stop()
	for {
		a.Health.Beat(HealthLoop)
		a.heartbeatOnce(ctx)
		if a.Health.Snapshot().Status == "ok" {
			_ = sdnotify.Watchdog() // systemd só é acalmado se todos os loops estão vivos
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// HeartbeatRequest builds the heartbeat payload.
func (a *Agent) HeartbeatRequest(ctx context.Context) protocol.HeartbeatRequest {
	cpu, mem := a.meter.Sample()
	pending, _ := a.Store.Count(ctx)
	req := protocol.HeartbeatRequest{
		V: protocol.Version, Ts: a.Client.ServerNow().UTC(), Version: buildinfo.Version, ClusterRole: a.Collector.Role(),
		CPUPercent: cpu, MemoryBytes: mem, QueuePending: pending, QueueDropped: a.Uploader.Dropped(),
		UptimeSeconds: int64(time.Since(a.started).Seconds()), LocalIPs: osinfo.LocalIPv4(),
		Hostname: osinfo.Hostname(), OS: osinfo.Describe(), Arch: runtime.GOARCH, HostMAC: osinfo.HostMAC(),
		AppliedConfigVersion: int(a.applied.Load()), DevicesKnown: a.Collector.KnownDevices(ctx),
		Paused: a.Collector.Paused(),
	}
	if t := a.Collector.LastScan(); !t.IsZero() {
		req.LastScanAt = &t
	}
	if t := a.Collector.LastRead(); !t.IsZero() {
		req.LastReadAt = &t
	}
	if e := a.Uploader.LastError(); e != "" {
		req.Errors = append(req.Errors, "envio: "+e)
	}
	return req
}

func (a *Agent) heartbeatOnce(ctx context.Context) {
	hctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	resp, err := a.Client.Heartbeat(hctx, a.HeartbeatRequest(hctx))
	if err != nil {
		if ctx.Err() != nil {
			return
		}
		a.heartbeatError.Store(err.Error())
		if errors.Is(err, api.ErrRevoked) {
			a.Log.Error("este coletor foi revogado no portal; cadastre-o novamente para voltar a coletar")
			a.Collector.SetRole("standby", true)
			return
		}
		a.Log.Warn("heartbeat falhou (as leituras continuam na fila local)", "erro", err)
		return
	}
	a.heartbeatError.Store("")
	a.lastHeartbeat.Store(time.Now().UnixMilli())
	a.Collector.SetRole(resp.ClusterRole, resp.Paused)
	if int64(resp.ConfigVersion) != a.applied.Load() {
		cfg, err := a.Client.Config(hctx)
		if err != nil {
			a.Log.Error("baixar configuração do local", "erro", err)
			return
		}
		a.apply(cfg, true)
	}
}

func (a *Agent) apply(cfg *protocol.AgentConfig, persist bool) {
	if err := a.Collector.Apply(cfg); err != nil {
		a.Log.Error("configuração aplicada com problemas", "erro", err)
	}
	a.applied.Store(int64(cfg.ConfigVersion))
	if err := osinfo.KeepAwake(cfg.KeepAwake); err != nil {
		a.Log.Error("não foi possível impedir a suspensão do Windows", "erro", err)
	}
	if !persist {
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Local.Server = cfg
	if err := config.Save(a.Dir, a.Local); err != nil {
		a.Log.Error("salvar configuração local", "erro", err)
		return
	}
	a.Log.Info("configuração aplicada", "versao", cfg.ConfigVersion, "faixas", len(cfg.Ranges),
		"credenciais", len(cfg.Credentials), "perfis", len(cfg.Profiles))
}
