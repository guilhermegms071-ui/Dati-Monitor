// Package commands executes remote commands (PROMPT 4.7) idempotently: every command id runs at most
// once, each state change is saved locally before it is reported, and states that did not reach the
// server are re-sent until they do (through the WebSocket or the HTTPS contingency channel).
package commands

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

// Result is what a handler returns.
type Result struct {
	Data   map[string]any
	Output string
}

// Handler runs one command type. progress reports intermediate text (shown live in the portal).
type Handler func(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (Result, error)

// Spec describes a command type.
type Spec struct {
	Handler Handler
	// Timeout for the execution itself (after it starts); 0 = DefaultTimeout.
	Timeout time.Duration
}

// DefaultTimeout bounds a command run.
const DefaultTimeout = 10 * time.Minute

// RetryReportEvery is how often unreported states are re-sent.
const RetryReportEvery = 15 * time.Second

// ErrRejected marks a state the server refused for good (unknown command, other agent): it is not
// retried.
var ErrRejected = errors.New("servidor recusou a atualização")

// Reporter sends one update to the server.
type Reporter func(ctx context.Context, upd protocol.CommandUpdate) error

// Executor dispatches commands to handlers.
type Executor struct {
	Store  *store.Store
	Specs  map[string]Spec
	Report Reporter
	Log    *slog.Logger
	Now    func() time.Time // relógio do servidor (para expiração)

	mu      sync.Mutex
	running map[string]context.CancelFunc
	wg      sync.WaitGroup
	kick    chan struct{}
}

// New creates an executor.
func New(st *store.Store, specs map[string]Spec, report Reporter, log *slog.Logger, now func() time.Time) *Executor {
	if now == nil {
		now = time.Now
	}
	return &Executor{
		Store: st, Specs: specs, Report: report, Log: log, Now: now,
		running: map[string]context.CancelFunc{}, kick: make(chan struct{}, 1),
	}
}

// Types lists the supported command types.
func (e *Executor) Types() []string {
	out := make([]string, 0, len(e.Specs))
	for t := range e.Specs {
		out = append(out, t)
	}
	return out
}

// Recover is called at startup: commands left "acked"/"running" by a crash or restart are finished
// as failed (a command is never silently lost).
func (e *Executor) Recover(ctx context.Context) error {
	recs, err := e.Store.CommandsInState(ctx, protocol.StateAcked, protocol.StateRunning)
	if err != nil {
		return err
	}
	for _, r := range recs {
		msg := "o coletor foi reiniciado durante a execução do comando"
		if r.Type == "reconnect" {
			// reconnect derruba a própria conexão: depois do reinício, a reconexão aconteceu.
			e.save(ctx, protocol.CommandUpdate{ID: r.ID, State: protocol.StateSucceeded,
				Result: map[string]any{"reconnected": true}}, r.Type)
			continue
		}
		e.save(ctx, protocol.CommandUpdate{ID: r.ID, State: protocol.StateFailed, Error: msg}, r.Type)
	}
	if len(recs) > 0 {
		e.Log.Warn("comandos interrompidos por reinício foram finalizados", "quantidade", len(recs))
	}
	return nil
}

// Dispatch handles a delivered command (possibly a repeated delivery).
func (e *Executor) Dispatch(ctx context.Context, cmd protocol.CommandMessage) {
	rec, err := e.Store.GetCommand(ctx, cmd.ID)
	if err != nil {
		e.Log.Error("ler registro local do comando", "id", cmd.ID, "erro", err)
		return
	}
	if rec != nil {
		// Entrega repetida: não executa de novo; garante que o último estado chegue ao servidor.
		if rec.Reported {
			var last protocol.CommandUpdate
			if json.Unmarshal(rec.LastUpdate, &last) == nil {
				e.send(ctx, last, rec.Type)
			}
		}
		e.Kick()
		return
	}
	spec, ok := e.Specs[cmd.Type]
	if !ok {
		e.save(ctx, protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateFailed,
			Error: fmt.Sprintf("comando %q não é suportado por esta versão do coletor", cmd.Type)}, cmd.Type)
		e.Kick()
		return
	}
	if !cmd.ExpiresAt.IsZero() && e.Now().After(cmd.ExpiresAt) {
		e.save(ctx, protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateFailed,
			Error: "o comando expirou antes de chegar ao coletor"}, cmd.Type)
		e.Kick()
		return
	}
	e.Log.Info("comando recebido", "id", cmd.ID, "tipo", cmd.Type)
	e.save(ctx, protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateAcked}, cmd.Type)
	e.Kick()
	timeout := spec.Timeout
	if timeout <= 0 {
		timeout = DefaultTimeout
	}
	rctx, cancel := context.WithTimeout(ctx, timeout)
	e.mu.Lock()
	e.running[cmd.ID] = cancel
	e.mu.Unlock()
	e.wg.Add(1)
	go func() {
		defer e.wg.Done()
		defer cancel()
		defer func() {
			e.mu.Lock()
			delete(e.running, cmd.ID)
			e.mu.Unlock()
		}()
		e.run(rctx, ctx, cmd, spec)
	}()
}

func (e *Executor) run(rctx, parent context.Context, cmd protocol.CommandMessage, spec Spec) {
	e.save(parent, protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateRunning}, cmd.Type)
	e.Kick()
	progress := func(text string) {
		e.save(parent, protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateRunning, Progress: text}, cmd.Type)
		e.Kick()
	}
	res, err := safeRun(rctx, spec.Handler, cmd, progress)
	upd := protocol.CommandUpdate{ID: cmd.ID, State: protocol.StateSucceeded, Result: res.Data, Output: res.Output}
	if err != nil {
		upd.State = protocol.StateFailed
		switch {
		case errors.Is(rctx.Err(), context.Canceled) && parent.Err() == nil:
			upd.Error = "comando cancelado no portal"
		case errors.Is(rctx.Err(), context.DeadlineExceeded):
			upd.Error = "tempo limite de execução esgotado: " + err.Error()
		default:
			upd.Error = err.Error()
		}
		e.Log.Warn("comando falhou", "id", cmd.ID, "tipo", cmd.Type, "erro", upd.Error)
	} else {
		e.Log.Info("comando concluído", "id", cmd.ID, "tipo", cmd.Type)
	}
	e.save(parent, upd, cmd.Type)
	e.Kick()
}

// safeRun turns a handler panic into an error (a buggy handler never takes the agent down).
func safeRun(ctx context.Context, h Handler, cmd protocol.CommandMessage, progress func(string)) (res Result, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("falha interna ao executar o comando: %v", r)
		}
	}()
	return h(ctx, cmd, progress)
}

// Cancel stops a running command (portal cancellation).
func (e *Executor) Cancel(id string) {
	e.mu.Lock()
	cancel, ok := e.running[id]
	e.mu.Unlock()
	if ok {
		e.Log.Info("comando cancelado no portal", "id", id)
		cancel()
	}
}

// Kick asks the report loop to flush now.
func (e *Executor) Kick() {
	select {
	case e.kick <- struct{}{}:
	default:
	}
}

func (e *Executor) save(ctx context.Context, upd protocol.CommandUpdate, typ string) {
	raw, err := json.Marshal(upd)
	if err != nil {
		e.Log.Error("serializar estado do comando", "id", upd.ID, "erro", err)
		return
	}
	if err := e.Store.PutCommandState(ctx, upd.ID, typ, upd.State, raw, time.Now()); err != nil {
		e.Log.Error("NÃO foi possível gravar o estado do comando na base local", "id", upd.ID, "erro", err)
	}
}

func (e *Executor) send(ctx context.Context, upd protocol.CommandUpdate, typ string) bool {
	rctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	err := e.Report(rctx, upd)
	if err != nil && !errors.Is(err, ErrRejected) {
		e.Log.Debug("estado do comando não enviado; nova tentativa em breve", "id", upd.ID, "erro", err)
		return false
	}
	if err != nil {
		e.Log.Error("servidor recusou o estado do comando; não será reenviado", "id", upd.ID, "tipo", typ, "erro", err)
	}
	if err := e.Store.MarkCommandReported(ctx, upd.ID, upd.State); err != nil {
		e.Log.Error("marcar estado do comando como enviado", "id", upd.ID, "erro", err)
	}
	return true
}

// Flush sends every unreported state once.
func (e *Executor) Flush(ctx context.Context) {
	recs, err := e.Store.UnreportedCommands(ctx)
	if err != nil {
		e.Log.Error("listar estados de comandos não enviados", "erro", err)
		return
	}
	for _, r := range recs {
		var upd protocol.CommandUpdate
		if err := json.Unmarshal(r.LastUpdate, &upd); err != nil {
			e.Log.Error("estado local de comando corrompido; descartado", "id", r.ID, "erro", err)
			_ = e.Store.MarkCommandReported(ctx, r.ID, r.State)
			continue
		}
		if !e.send(ctx, upd, r.Type) {
			return // canal fora: tenta tudo de novo depois, em ordem
		}
	}
}

// Run flushes unreported states on every kick and periodically, and prunes old records.
func (e *Executor) Run(ctx context.Context) {
	t := time.NewTicker(RetryReportEvery)
	defer t.Stop()
	prune := time.NewTicker(time.Hour)
	defer prune.Stop()
	for {
		e.Flush(ctx)
		select {
		case <-ctx.Done():
			e.wg.Wait()
			return
		case <-e.kick:
		case <-t.C:
		case <-prune.C:
			if n, err := e.Store.PruneCommands(ctx, time.Now()); err != nil {
				e.Log.Error("limpar registros antigos de comandos", "erro", err)
			} else if n > 0 {
				e.Log.Debug("registros antigos de comandos removidos", "quantidade", n)
			}
		}
	}
}
