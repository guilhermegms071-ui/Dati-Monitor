// Package watchdog is dm-watchdog (PROMPT 5.1): it keeps the agent alive and executes the commands the
// agent cannot run on itself (restart, update, rollback, uninstall).
//
// Every 15 s it checks the agent: service stopped, /health failing 3 times in a row or memory above
// 300 MB → restart, with the reason recorded and reported. Every 60 s it talks to the server on its own
// simple channel (POST /api/watchdog/heartbeat), independent from the agent's WebSocket and code, and
// receives its commands. It is deliberately small: it is the piece that cannot fail.
package watchdog

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"sync/atomic"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/logx"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/release"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// Defaults of PROMPT 5.1.
const (
	DefaultCheckEvery  = 15 * time.Second
	DefaultReportEvery = 60 * time.Second
	DefaultFailures    = 3
	DefaultMemoryLimit = 300 << 20
	DefaultStartGrace  = 60 * time.Second
	DefaultUpdateWait  = 2 * time.Minute
	// HealthAddr is the watchdog's own /health (the agent checks it after updating the watchdog).
	HealthAddr = "127.0.0.1:47702"
	// Loop names in the watchdog's /health.
	LoopCheck  = "check"
	LoopReport = "report"
)

// Options configure the watchdog.
type Options struct {
	DataDir     string
	AgentHealth string // endereço do /health do coletor (127.0.0.1:47701)
	Client      *api.Client
	Target      Target
	Log         *slog.Logger
	Version     string
	PublicKey   ed25519.PublicKey
	Health      *health.Registry

	CheckEvery  time.Duration
	ReportEvery time.Duration
	Failures    int
	MemoryLimit uint64
	StartGrace  time.Duration
	UpdateWait  time.Duration
	// OnUninstall runs after `uninstall` was reported: removes the watchdog itself and stops the process.
	OnUninstall func()
}

// Watchdog supervises the agent.
type Watchdog struct {
	o         Options
	hc        *http.Client
	installer *release.Installer
	queue     chan protocol.CommandMessage
	journal   *journal
	updating  atomic.Bool

	mu              sync.Mutex
	failures        int
	lastStart       time.Time
	pendingRestarts []protocol.WatchdogRestart
	status          svc.Status
	statusErr       error
	agentReport     *health.Report
	agentErr        error
	memory          uint64
	lastReportAt    time.Time
	lastReportErr   string
	queued          map[string]bool
}

// New prepares a watchdog (defaults of PROMPT 5.1 for zero values).
func New(o Options) (*Watchdog, error) {
	if o.Client == nil || o.Target == nil || o.Log == nil {
		return nil, errors.New("watchdog sem cliente, alvo ou log")
	}
	if len(o.PublicKey) == 0 {
		key, err := release.PublicKey()
		if err != nil {
			return nil, err
		}
		o.PublicKey = key
	}
	setDefault(&o.CheckEvery, DefaultCheckEvery)
	setDefault(&o.ReportEvery, DefaultReportEvery)
	setDefault(&o.StartGrace, DefaultStartGrace)
	setDefault(&o.UpdateWait, DefaultUpdateWait)
	if o.Failures <= 0 {
		o.Failures = DefaultFailures
	}
	if o.MemoryLimit == 0 {
		o.MemoryLimit = DefaultMemoryLimit
	}
	if o.AgentHealth == "" {
		o.AgentHealth = health.DefaultAddr
	}
	if o.Health == nil {
		o.Health = health.NewRegistry()
	}
	j, err := openJournal(filepath.Join(o.DataDir, "watchdog-commands.json"))
	if err != nil {
		return nil, err
	}
	w := &Watchdog{
		o:       o,
		hc:      &http.Client{Timeout: 5 * time.Second},
		queue:   make(chan protocol.CommandMessage, 32),
		journal: j,
		queued:  map[string]bool{},
	}
	w.installer = &release.Installer{
		Service:   o.Target,
		StateDir:  filepath.Join(o.DataDir, "updates", "agent"),
		PublicKey: o.PublicKey,
		Download:  o.Client.Download,
		Healthy:   w.waitAgentVersion,
		Wait:      o.UpdateWait,
		Log:       o.Log,
	}
	o.Health.Register(LoopCheck, o.CheckEvery)
	o.Health.Register(LoopReport, o.ReportEvery)
	o.Health.SetInfo(w.info)
	return w, nil
}

func setDefault(d *time.Duration, v time.Duration) {
	if *d <= 0 {
		*d = v
	}
}

// Run supervises until ctx ends.
func (w *Watchdog) Run(ctx context.Context) error {
	w.o.Log.Info("watchdog iniciando", "versao", w.o.Version, "coletor", w.o.AgentHealth)
	var wg sync.WaitGroup
	wg.Add(3)
	go func() { defer wg.Done(); w.loop(ctx, w.o.CheckEvery, LoopCheck, w.check) }()
	go func() { defer wg.Done(); w.loop(ctx, w.o.ReportEvery, LoopReport, w.report) }()
	go func() { defer wg.Done(); w.executor(ctx) }()
	wg.Wait()
	w.o.Log.Info("watchdog encerrado")
	return nil
}

func (w *Watchdog) loop(ctx context.Context, every time.Duration, name string, fn func(context.Context)) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		fn(ctx)
		w.o.Health.Beat(name)
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// ----------------------------------------------------------------------------- vigilância

func (w *Watchdog) check(ctx context.Context) {
	if w.updating.Load() {
		return // a atualização para e inicia o coletor por conta própria
	}
	st, err := w.o.Target.Status(ctx)
	w.mu.Lock()
	w.status, w.statusErr = st, err
	sinceStart := time.Since(w.lastStart)
	w.mu.Unlock()
	if err != nil {
		w.o.Log.Error("consultar o serviço do coletor", "erro", err)
		return
	}
	switch st.State {
	case svc.StateNotInstalled:
		return // nada a vigiar; informado ao servidor no heartbeat
	case svc.StateStopped:
		_ = w.restart(ctx, "o serviço do coletor estava parado")
		return
	case svc.StateStarting, svc.StateStopping:
		return
	}
	if sinceStart < w.o.StartGrace {
		return
	}
	rep, herr := w.agentHealth(ctx)
	w.mu.Lock()
	w.agentReport, w.agentErr = rep, herr
	if herr != nil {
		w.failures++
	} else {
		w.failures = 0
	}
	failures := w.failures
	w.mu.Unlock()
	if herr != nil {
		w.o.Log.Warn("coletor não saudável", "tentativa", failures, "erro", herr)
		if failures >= w.o.Failures {
			_ = w.restart(ctx, fmt.Sprintf("/health falhou %d vezes seguidas: %v", failures, herr))
		}
		return
	}
	if st.PID > 0 {
		mem, err := osinfo.ProcessMemory(st.PID)
		if err != nil {
			w.o.Log.Warn("medir memória do coletor", "erro", err)
			return
		}
		w.mu.Lock()
		w.memory = mem
		w.mu.Unlock()
		if mem > w.o.MemoryLimit {
			_ = w.restart(ctx, fmt.Sprintf("memória acima de %d MB (%d MB)", w.o.MemoryLimit>>20, mem>>20))
		}
	}
}

// restart stops (if running) and starts the agent, recording the reason for the server.
func (w *Watchdog) restart(ctx context.Context, reason string) error {
	w.o.Log.Warn("reiniciando o coletor", "motivo", reason)
	if err := w.o.Target.Stop(ctx); err != nil {
		w.o.Log.Error("parar o coletor", "erro", err)
	}
	err := w.o.Target.Start(ctx)
	w.mu.Lock()
	w.lastStart = time.Now()
	w.failures = 0
	w.agentReport = nil
	if err == nil {
		w.pendingRestarts = append(w.pendingRestarts, protocol.WatchdogRestart{At: time.Now().UTC(), Reason: reason})
	}
	w.mu.Unlock()
	if err != nil {
		w.o.Log.Error("iniciar o coletor", "motivo", reason, "erro", err)
		return err
	}
	return nil
}

// agentHealth reads the agent's /health (the body is the report also when it answers 503).
func (w *Watchdog) agentHealth(ctx context.Context) (*health.Report, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+w.o.AgentHealth+"/health", nil)
	if err != nil {
		return nil, err
	}
	resp, err := w.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("sem resposta: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	var rep health.Report
	if err := json.NewDecoder(resp.Body).Decode(&rep); err != nil {
		return nil, fmt.Errorf("resposta inválida (HTTP %d): %w", resp.StatusCode, err)
	}
	if rep.Status != "ok" {
		return &rep, fmt.Errorf("não saudável: %v", rep.Unhealthy)
	}
	return &rep, nil
}

func infoString(rep *health.Report, key string) string {
	if rep == nil {
		return ""
	}
	s, _ := rep.Info[key].(string)
	return s
}

// waitAgentVersion is the installer's check: `version` healthy and a heartbeat accepted after `since`.
func (w *Watchdog) waitAgentVersion(ctx context.Context, version string, since time.Time) error {
	var last error
	for {
		rep, err := w.agentHealth(ctx)
		switch {
		case err != nil:
			last = err
		case infoString(rep, "version") != version:
			last = fmt.Errorf("rodando a versão %q", infoString(rep, "version"))
		default:
			hb, perr := time.Parse(time.RFC3339Nano, infoString(rep, "last_heartbeat_at"))
			if perr == nil && hb.After(since) {
				return nil
			}
			last = errors.New("ainda sem heartbeat aceito pelo servidor")
		}
		select {
		case <-ctx.Done():
			return last
		case <-time.After(2 * time.Second):
		}
	}
}

// ----------------------------------------------------------------------------- canal com o servidor

func reportState(st svc.Status, err error) string {
	if err != nil {
		return protocol.ServiceUnknown
	}
	switch st.State {
	case svc.StateRunning, svc.StateStarting, svc.StateNotInstalled:
		return st.State
	case svc.StateStopped, svc.StateStopping:
		return protocol.ServiceStopped
	default:
		return protocol.ServiceUnknown
	}
}

func (w *Watchdog) report(ctx context.Context) {
	w.mu.Lock()
	restarts := append([]protocol.WatchdogRestart(nil), w.pendingRestarts...)
	req := protocol.WatchdogHeartbeatRequest{
		Ts:                   time.Now().UTC(),
		Version:              w.o.Version,
		OS:                   runtime.GOOS,
		Arch:                 runtime.GOARCH,
		AgentState:           reportState(w.status, w.statusErr),
		AgentHealthy:         w.agentReport != nil && w.agentErr == nil,
		AgentVersion:         infoString(w.agentReport, "version"),
		AgentMemoryBytes:     w.memory,
		PreviousAgentVersion: w.installer.PreviousVersion(),
		Restarts:             restarts,
		Errors:               []string{},
	}
	if w.statusErr != nil {
		req.Errors = append(req.Errors, "serviço do coletor: "+w.statusErr.Error())
	}
	if w.agentErr != nil {
		req.Errors = append(req.Errors, "saúde do coletor: "+w.agentErr.Error())
	}
	w.mu.Unlock()
	resp, err := w.o.Client.WatchdogHeartbeat(ctx, req)
	w.mu.Lock()
	if err != nil {
		w.lastReportErr = err.Error()
		w.mu.Unlock()
		w.o.Log.Error("heartbeat do watchdog falhou", "erro", err)
		return
	}
	w.pendingRestarts = w.pendingRestarts[len(restarts):]
	w.lastReportAt, w.lastReportErr = time.Now(), ""
	w.mu.Unlock()
	w.retryUnreported(ctx)
	for _, cmd := range resp.Commands {
		w.enqueue(cmd)
	}
}

func (w *Watchdog) info() map[string]any {
	w.mu.Lock()
	defer w.mu.Unlock()
	var last any
	if !w.lastReportAt.IsZero() {
		last = w.lastReportAt.UTC().Format(time.RFC3339Nano)
	}
	return map[string]any{
		"version":           w.o.Version,
		"last_report_at":    last,
		"last_report_error": w.lastReportErr,
		"agent_state":       w.status.State,
		"agent_failures":    w.failures,
		"updating":          w.updating.Load(),
	}
}

// ----------------------------------------------------------------------------- comandos

func (w *Watchdog) enqueue(cmd protocol.CommandMessage) {
	w.mu.Lock()
	if w.queued[cmd.ID] {
		w.mu.Unlock()
		return
	}
	w.queued[cmd.ID] = true
	w.mu.Unlock()
	select {
	case w.queue <- cmd:
	default:
		w.o.Log.Error("fila de comandos do watchdog cheia; comando fica para a próxima entrega", "id", cmd.ID)
		w.mu.Lock()
		delete(w.queued, cmd.ID)
		w.mu.Unlock()
	}
}

func (w *Watchdog) executor(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case cmd := <-w.queue:
			w.execute(ctx, cmd)
			w.mu.Lock()
			delete(w.queued, cmd.ID)
			w.mu.Unlock()
		}
	}
}

func (w *Watchdog) send(ctx context.Context, upd protocol.CommandUpdate) error {
	upd.V = protocol.Version
	_, err := w.o.Client.CommandUpdate(ctx, upd)
	return err
}

func (w *Watchdog) execute(ctx context.Context, cmd protocol.CommandMessage) {
	if final, ok := w.journal.get(cmd.ID); ok {
		// Já executado (o resultado pode não ter chegado): só reenvia, nunca executa de novo.
		if err := w.send(ctx, final); err == nil {
			w.logJournal(w.journal.markReported(cmd.ID))
		}
		return
	}
	log := w.o.Log.With("comando", cmd.Type, "id", cmd.ID)
	log.Info("executando comando do watchdog")
	if err := w.send(ctx, protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateRunning}); err != nil {
		log.Warn("informar início do comando", "erro", err)
	}
	result, err := w.run(ctx, cmd)
	final := protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateSucceeded, Result: result}
	if err != nil {
		final.State, final.Error = protocol.StateFailed, err.Error()
		log.Error("comando do watchdog falhou", "erro", err)
	} else {
		log.Info("comando do watchdog concluído")
	}
	w.logJournal(w.journal.put(final))
	if err := w.send(ctx, final); err != nil {
		log.Error("enviar resultado do comando (nova tentativa no próximo heartbeat)", "erro", err)
	} else {
		w.logJournal(w.journal.markReported(cmd.ID))
	}
	if cmd.Type == "uninstall" && final.State == protocol.StateSucceeded && w.o.OnUninstall != nil {
		w.o.OnUninstall()
	}
}

func (w *Watchdog) retryUnreported(ctx context.Context) {
	for _, upd := range w.journal.unreported() {
		if err := w.send(ctx, upd); err != nil {
			w.o.Log.Warn("reenviar resultado de comando", "id", upd.ID, "erro", err)
			continue
		}
		w.logJournal(w.journal.markReported(upd.ID))
	}
}

func (w *Watchdog) logJournal(err error) {
	if err != nil {
		w.o.Log.Error("registro local de comandos", "erro", err)
	}
}

func (w *Watchdog) currentAgentVersion(ctx context.Context) string {
	rep, _ := w.agentHealth(ctx)
	if v := infoString(rep, "version"); v != "" {
		return v
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	return infoString(w.agentReport, "version")
}

func (w *Watchdog) run(ctx context.Context, cmd protocol.CommandMessage) (map[string]any, error) {
	switch cmd.Type {
	case "restart_agent":
		started := time.Now()
		if err := w.restart(ctx, "pedido pelo portal (Reiniciar/Reativar)"); err != nil {
			return nil, err
		}
		wctx, cancel := context.WithTimeout(ctx, 90*time.Second)
		defer cancel()
		for {
			if _, err := w.agentHealth(wctx); err == nil {
				return map[string]any{"restarted": true, "healthy": true, "seconds": time.Since(started).Seconds()}, nil
			}
			select {
			case <-wctx.Done():
				return nil, errors.New("coletor reiniciado, mas o /health não ficou saudável em 90 s")
			case <-time.After(2 * time.Second):
			}
		}
	case "update":
		var p protocol.UpdateParams
		if err := json.Unmarshal(cmd.Params, &p); err != nil {
			return nil, fmt.Errorf("parâmetros de update inválidos: %w", err)
		}
		if p.Component != "agent" {
			return nil, fmt.Errorf("o watchdog atualiza só o coletor (recebido %q)", p.Component)
		}
		w.updating.Store(true)
		defer w.updating.Store(false)
		res, err := w.installer.Update(ctx, p, w.currentAgentVersion(ctx))
		w.markStarted()
		return res.Map(), err
	case "rollback":
		w.updating.Store(true)
		defer w.updating.Store(false)
		res, err := w.installer.Rollback(ctx, w.currentAgentVersion(ctx))
		w.markStarted()
		return res.Map(), err
	case "get_logs":
		var p struct {
			Hours int `json:"hours"`
		}
		_ = json.Unmarshal(cmd.Params, &p)
		if p.Hours <= 0 {
			p.Hours = 24
		}
		var buf bytes.Buffer
		n, err := logx.ZipRecent(filepath.Join(w.o.DataDir, "logs"), time.Now().Add(-time.Duration(p.Hours)*time.Hour), 45<<20, &buf)
		if err != nil {
			return nil, fmt.Errorf("compactar logs: %w", err)
		}
		up, err := w.o.Client.Upload(ctx, "logs", cmd.ID, "application/zip", buf.Bytes())
		if err != nil {
			return nil, fmt.Errorf("enviar logs: %w", err)
		}
		return map[string]any{"files": n, "bytes": buf.Len(), "log_id": up.ID}, nil
	case "uninstall":
		if err := w.o.Target.Uninstall(ctx); err != nil {
			return nil, fmt.Errorf("remover o serviço do coletor: %w", err)
		}
		return map[string]any{"agent_service_removed": true}, nil
	default:
		return nil, fmt.Errorf("comando %q não é executado pelo watchdog", cmd.Type)
	}
}

func (w *Watchdog) markStarted() {
	w.mu.Lock()
	w.lastStart = time.Now()
	w.failures = 0
	w.mu.Unlock()
}

// ----------------------------------------------------------------------------- registro de comandos

// journal remembers finished commands (idempotency by id) and which results still must be reported.
type journal struct {
	path string
	mu   sync.Mutex
	data journalData
}

type journalData struct {
	Entries []journalEntry `json:"entries"`
}

type journalEntry struct {
	Update   protocol.CommandUpdate `json:"update"`
	Reported bool                   `json:"reported"`
}

const journalMax = 200

func openJournal(path string) (*journal, error) {
	j := &journal{path: path}
	raw, err := os.ReadFile(path) //nolint:gosec // G304: arquivo do próprio watchdog
	if errors.Is(err, os.ErrNotExist) {
		return j, nil
	}
	if err != nil {
		return nil, fmt.Errorf("ler registro de comandos do watchdog: %w", err)
	}
	if err := json.Unmarshal(raw, &j.data); err != nil {
		return nil, fmt.Errorf("registro de comandos do watchdog corrompido (%s): %w", path, err)
	}
	return j, nil
}

func (j *journal) get(id string) (protocol.CommandUpdate, bool) {
	j.mu.Lock()
	defer j.mu.Unlock()
	for _, e := range j.data.Entries {
		if e.Update.ID == id {
			return e.Update, true
		}
	}
	return protocol.CommandUpdate{}, false
}

func (j *journal) put(u protocol.CommandUpdate) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	j.data.Entries = append(j.data.Entries, journalEntry{Update: u})
	if len(j.data.Entries) > journalMax {
		j.data.Entries = j.data.Entries[len(j.data.Entries)-journalMax:]
	}
	return j.saveLocked()
}

func (j *journal) markReported(id string) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	for i := range j.data.Entries {
		if j.data.Entries[i].Update.ID == id {
			j.data.Entries[i].Reported = true
		}
	}
	return j.saveLocked()
}

func (j *journal) unreported() []protocol.CommandUpdate {
	j.mu.Lock()
	defer j.mu.Unlock()
	var out []protocol.CommandUpdate
	for _, e := range j.data.Entries {
		if !e.Reported {
			out = append(out, e.Update)
		}
	}
	return out
}

func (j *journal) saveLocked() error {
	raw, err := json.Marshal(j.data)
	if err != nil {
		return err
	}
	tmp := j.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return fmt.Errorf("gravar registro de comandos do watchdog: %w", err)
	}
	if err := os.Rename(tmp, j.path); err != nil {
		return fmt.Errorf("gravar registro de comandos do watchdog: %w", err)
	}
	return nil
}
