// Package agent wires the collector, the outbox uploader, the WebSocket channel, the remote-command
// executor, the heartbeat/config loop and the local health endpoint into the dm-agent process.
package agent

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/collector"
	"github.com/daticopy/dati-monitor/agent/internal/commands"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/sdnotify"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
	"github.com/daticopy/dati-monitor/agent/internal/store"
	"github.com/daticopy/dati-monitor/agent/internal/uploader"
	"github.com/daticopy/dati-monitor/agent/internal/watchdog"
	"github.com/daticopy/dati-monitor/agent/internal/ws"
)

// Intervals of PROMPT 4.3.
const (
	HeartbeatInterval = 30 * time.Second
	// With the WebSocket down for longer than this, heartbeats and commands go over HTTPS.
	ContingencyAfter = 2 * time.Minute
	PollInterval     = 60 * time.Second
)

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
	WS        *ws.Channel
	Exec      *commands.Executor
	// ContingencyAfter/PollInterval/ReconnectWait are fields so tests can shorten them.
	ContingencyAfter time.Duration
	PollInterval     time.Duration
	ReconnectWait    time.Duration
	// Mutual watch and watchdog updates (PROMPT 5.1/5.2); tests replace the service and the key.
	WatchdogService    watchdog.Target
	WatchdogHealth     string
	WatchdogCheckEvery time.Duration
	ReleaseKey         ed25519.PublicKey

	meter          osinfo.ProcessMeter
	started        time.Time
	applied        atomic.Int64
	lastHeartbeat  atomic.Int64
	heartbeatError atomic.Value
	wsLastError    atomic.Value
	watchdogState  atomic.Value
	hbKick         chan struct{}
	mu             sync.Mutex
	runCtx         context.Context
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
	a := &Agent{
		Dir: dir, Local: local, Log: log, Client: client, Store: st, Health: health.NewRegistry(), started: time.Now(),
		ContingencyAfter: ContingencyAfter, PollInterval: PollInterval, hbKick: make(chan struct{}, 1),
		WatchdogService: watchdog.ServiceTarget{Name: product.WatchdogServiceName()}, WatchdogHealth: watchdog.HealthAddr,
	}
	a.heartbeatError.Store("")
	a.wsLastError.Store("")
	a.Uploader = &uploader.Uploader{Store: st, Send: client, AgentID: local.AgentID, Log: log, Health: a.Health}
	a.Collector = collector.New(collector.Deps{
		Store: st, Log: log, Health: a.Health, Clock: client.ServerNow,
		Suggest: client.SuggestRanges, OnEnqueue: a.Uploader.Kick,
	})
	a.Exec = commands.New(st, a.commandSpecs(), a.reportCommand, log, client.ServerNow)
	a.WS = ws.New(client, a.wsURL, (*wsHandler)(a), log)
	a.WS.OnError = func(msg string) { a.wsLastError.Store(msg) }
	a.WS.Capabilities = a.Exec.Types()
	sort.Strings(a.WS.Capabilities)
	a.Health.SetInfo(a.info)
	return a, nil
}

// wsURL is the WebSocket endpoint: from the server configuration, else from enrollment, else derived.
func (a *Agent) wsURL() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	configured := a.Local.WSURL
	if a.Local.Server != nil && a.Local.Server.WSURL != "" {
		configured = a.Local.Server.WSURL
	}
	return ws.Endpoint(a.Local.ServerURL, configured)
}

func (a *Agent) wsError() string {
	s, _ := a.wsLastError.Load().(string)
	return s
}

func (a *Agent) info() map[string]any {
	pending, _ := a.Store.Count(context.Background())
	dead, _ := a.Store.DeadCount(context.Background())
	hbErr, _ := a.heartbeatError.Load().(string)
	return map[string]any{
		"version":              buildinfo.Version,
		"pid":                  os.Getpid(),
		"watchdog_state":       a.WatchdogState(),
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
		"ws_connected":         a.WS.Connected(),
		"ws_down_seconds":      a.WS.DownFor().Seconds(),
		"ws_rtt_ms":            a.WS.RTT(),
		"ws_last_error":        a.wsError(),
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
		_ = a.apply(a.Local.Server, false)
	}
	if err := a.Exec.Recover(ctx); err != nil {
		a.Log.Error("recuperar comandos interrompidos", "erro", err)
	}
	addr := a.Local.HealthAddr
	if addr == "" {
		addr = health.DefaultAddr
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	a.mu.Lock()
	a.runCtx = ctx
	a.mu.Unlock()
	var wg sync.WaitGroup
	errs := make(chan error, 1)
	wg.Add(7)
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
	go func() { defer wg.Done(); a.WS.Run(ctx) }()
	go func() { defer wg.Done(); a.Exec.Run(ctx) }()
	go func() { defer wg.Done(); a.pollLoop(ctx) }()
	go func() { defer wg.Done(); a.watchdogLoop(ctx) }()
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
		case <-a.hbKick:
		}
	}
}

// contingency reports whether the WebSocket has been down long enough to use the HTTPS channel.
func (a *Agent) contingency() bool {
	return !a.WS.Connected() && a.WS.DownFor() >= a.ContingencyAfter
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
		Paused: a.Collector.Paused(), LatencyMS: a.WS.RTT(), WSConnected: a.WS.Connected(),
		WatchdogState: a.WatchdogState(), InstallPath: installPath(),
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
	var resp *protocol.HeartbeatResponse
	var err error
	switch {
	case a.WS.Connected():
		resp, err = a.WS.Heartbeat(hctx, a.HeartbeatRequest(hctx))
		if err != nil && ctx.Err() == nil {
			// Canal "conectado" que não confirma: garante o lease pelo HTTPS nesta rodada.
			a.Log.Warn("heartbeat pelo WebSocket falhou; enviando pelo HTTPS", "erro", err)
			resp, err = a.Client.Heartbeat(hctx, a.HeartbeatRequest(hctx))
		}
	case a.contingency():
		resp, err = a.Client.Heartbeat(hctx, a.HeartbeatRequest(hctx))
	default:
		return // WebSocket reconectando há menos de 2 min: o heartbeat sai assim que ele voltar
	}
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
		_ = a.apply(cfg, true)
	}
}

// pollLoop is the HTTPS contingency channel for commands (PROMPT 4.3): with the WebSocket down for
// more than 2 min, pending commands are fetched every 60 s.
func (a *Agent) pollLoop(ctx context.Context) {
	t := time.NewTicker(a.PollInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		if !a.contingency() || a.WS.Revoked() {
			continue
		}
		pctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		cmds, err := a.Client.PendingCommands(pctx)
		cancel()
		if err != nil {
			if ctx.Err() == nil {
				a.Log.Warn("buscar comandos pelo HTTPS falhou", "erro", err)
			}
			continue
		}
		for _, c := range cmds {
			a.Exec.Dispatch(ctx, c)
		}
	}
}

// reportCommand sends a command update: WebSocket when up, HTTPS otherwise.
func (a *Agent) reportCommand(ctx context.Context, upd protocol.CommandUpdate) error {
	if a.WS.Connected() {
		err := a.WS.CommandUpdate(ctx, upd)
		if err == nil {
			return nil
		}
		if errors.Is(err, ws.ErrRejected) {
			return fmt.Errorf("%w: %w", commands.ErrRejected, err)
		}
	}
	_, err := a.Client.CommandUpdate(ctx, upd)
	var apiErr *api.Error
	if errors.As(err, &apiErr) && apiErr.Permanent() {
		return fmt.Errorf("%w: %w", commands.ErrRejected, err)
	}
	return err
}

func (a *Agent) apply(cfg *protocol.AgentConfig, persist bool) error {
	applyErr := a.Collector.Apply(cfg)
	if applyErr != nil {
		a.Log.Error("configuração aplicada com problemas", "erro", applyErr)
	}
	a.applied.Store(int64(cfg.ConfigVersion))
	if err := osinfo.KeepAwake(cfg.KeepAwake); err != nil {
		a.Log.Error("não foi possível impedir a suspensão do Windows", "erro", err)
	}
	if !persist {
		return applyErr
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.Local.Server = cfg
	if err := config.Save(a.Dir, a.Local); err != nil {
		a.Log.Error("salvar configuração local", "erro", err)
		return fmt.Errorf("salvar configuração local: %w", err)
	}
	a.Log.Info("configuração aplicada", "versao", cfg.ConfigVersion, "faixas", len(cfg.Ranges),
		"credenciais", len(cfg.Credentials), "perfis", len(cfg.Profiles))
	return applyErr
}

// wsHandler receives the WebSocket events (methods must not block the read loop).
type wsHandler Agent

func (h *wsHandler) OnConnected() {
	a := (*Agent)(h)
	select { // heartbeat imediato: presença e lease atualizados assim que o canal volta
	case a.hbKick <- struct{}{}:
	default:
	}
	a.Exec.Kick()
}

func (h *wsHandler) OnCommand(cmd protocol.CommandMessage) {
	a := (*Agent)(h)
	a.mu.Lock()
	ctx := a.runCtx
	a.mu.Unlock()
	if ctx == nil || ctx.Err() != nil {
		return // encerrando: o comando fica "sent" e é reentregue quando o coletor voltar
	}
	go a.Exec.Dispatch(ctx, cmd)
}

func (h *wsHandler) OnCancel(id string) { (*Agent)(h).Exec.Cancel(id) }

// installPath is the folder of the running executable (shown in the portal, PROMPT 16.10).
func installPath() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return filepath.Dir(exe)
}
