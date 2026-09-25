package commands

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

// server records what reached it; fail makes Report return a transient error.
type server struct {
	mu      sync.Mutex
	updates []protocol.CommandUpdate
	fail    atomic.Bool
	reject  atomic.Bool
}

func (s *server) report(_ context.Context, u protocol.CommandUpdate) error {
	if s.fail.Load() {
		return errors.New("sem conexão")
	}
	if s.reject.Load() {
		return ErrRejected
	}
	s.mu.Lock()
	s.updates = append(s.updates, u)
	s.mu.Unlock()
	return nil
}

func (s *server) states(id string) []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []string
	for _, u := range s.updates {
		if u.ID == id {
			out = append(out, u.State)
		}
	}
	return out
}

func (s *server) final(id string) (protocol.CommandUpdate, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := len(s.updates) - 1; i >= 0; i-- {
		u := s.updates[i]
		if u.ID == id && (u.State == protocol.StateSucceeded || u.State == protocol.StateFailed) {
			return u, true
		}
	}
	return protocol.CommandUpdate{}, false
}

func setup(t *testing.T, specs map[string]Spec) (*Executor, *server, *store.Store, context.CancelFunc) {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	srv := &server{}
	e := New(st, specs, srv.report, slog.New(slog.NewTextHandler(io.Discard, nil)), nil)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { e.Run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); <-done; _ = st.Close() })
	return e, srv, st, cancel
}

func cmd(id, typ string) protocol.CommandMessage {
	return protocol.CommandMessage{ID: id, Type: typ, Params: json.RawMessage(`{"x":1}`), ExpiresAt: time.Now().Add(time.Minute)}
}

func waitFinal(t *testing.T, srv *server, id string) protocol.CommandUpdate {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for {
		if u, ok := srv.final(id); ok {
			return u
		}
		if time.Now().After(deadline) {
			t.Fatalf("comando %s não terminou", id)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestRunsOnceAndReportsEveryState(t *testing.T) {
	var runs atomic.Int32
	specs := map[string]Spec{"diagnostics": {Handler: func(_ context.Context, c protocol.CommandMessage, progress func(string)) (Result, error) {
		runs.Add(1)
		progress("etapa 1")
		var p struct{ X int }
		_ = json.Unmarshal(c.Params, &p)
		return Result{Data: map[string]any{"x": p.X}, Output: "ok"}, nil
	}}}
	e, srv, _, _ := setup(t, specs)
	ctx := context.Background()
	e.Dispatch(ctx, cmd("a", "diagnostics"))
	u := waitFinal(t, srv, "a")
	if u.State != protocol.StateSucceeded || u.Result["x"] != float64(1) || u.Output != "ok" {
		t.Fatalf("%+v", u)
	}
	// Entrega repetida (conexão caiu antes do ack): não executa de novo, só reenvia o estado final.
	e.Dispatch(ctx, cmd("a", "diagnostics"))
	e.Dispatch(ctx, cmd("a", "diagnostics"))
	time.Sleep(200 * time.Millisecond)
	if runs.Load() != 1 {
		t.Fatalf("executou %d vezes", runs.Load())
	}
	states := srv.states("a")
	if states[len(states)-1] != protocol.StateSucceeded {
		t.Fatalf("estados: %v", states)
	}
	if !slices.Contains(states, protocol.StateSucceeded) {
		t.Fatal("sem estado final")
	}
	if got := e.Types(); len(got) != 1 || got[0] != "diagnostics" {
		t.Fatalf("tipos: %v", got)
	}
}

func TestUnknownExpiredPanicAndError(t *testing.T) {
	specs := map[string]Spec{
		"boom": {Handler: func(context.Context, protocol.CommandMessage, func(string)) (Result, error) { panic("quebrou") }},
		"falha": {Handler: func(context.Context, protocol.CommandMessage, func(string)) (Result, error) {
			return Result{}, errors.New("impressora não respondeu")
		}},
		"lento": {Timeout: 50 * time.Millisecond, Handler: func(ctx context.Context, _ protocol.CommandMessage, _ func(string)) (Result, error) {
			<-ctx.Done()
			return Result{}, ctx.Err()
		}},
	}
	e, srv, _, _ := setup(t, specs)
	ctx := context.Background()
	e.Dispatch(ctx, cmd("u", "desconhecido"))
	old := cmd("x", "falha")
	old.ExpiresAt = time.Now().Add(-time.Second)
	e.Dispatch(ctx, old)
	e.Dispatch(ctx, cmd("p", "boom"))
	e.Dispatch(ctx, cmd("f", "falha"))
	e.Dispatch(ctx, cmd("l", "lento"))
	want := map[string]string{
		"u": "não é suportado", "x": "expirou antes de chegar", "p": "falha interna", "f": "impressora não respondeu",
		"l": "tempo limite",
	}
	for id, text := range want {
		u := waitFinal(t, srv, id)
		if u.State != protocol.StateFailed || !strings.Contains(u.Error, text) {
			t.Errorf("%s: %+v", id, u)
		}
	}
}

func TestCancelStopsTheHandler(t *testing.T) {
	started := make(chan struct{})
	specs := map[string]Spec{"walk": {Handler: func(ctx context.Context, _ protocol.CommandMessage, _ func(string)) (Result, error) {
		close(started)
		<-ctx.Done()
		return Result{}, ctx.Err()
	}}}
	e, srv, _, _ := setup(t, specs)
	e.Dispatch(context.Background(), cmd("w", "walk"))
	<-started
	e.Cancel("w")
	e.Cancel("inexistente")
	u := waitFinal(t, srv, "w")
	if u.State != protocol.StateFailed || u.Error != "comando cancelado no portal" {
		t.Fatalf("%+v", u)
	}
}

func TestStatesSurviveDisconnectionAndRestart(t *testing.T) {
	block := make(chan struct{})
	specs := map[string]Spec{
		"ok": {Handler: func(context.Context, protocol.CommandMessage, func(string)) (Result, error) { return Result{}, nil }},
		"travado": {Handler: func(_ context.Context, _ protocol.CommandMessage, _ func(string)) (Result, error) {
			<-block
			return Result{}, nil
		}},
		"reconnect": {Handler: func(_ context.Context, _ protocol.CommandMessage, _ func(string)) (Result, error) {
			<-block
			return Result{}, nil
		}},
	}
	e, srv, st, _ := setup(t, specs)
	srv.fail.Store(true) // canal fora do ar
	e.Dispatch(context.Background(), cmd("k", "ok"))
	time.Sleep(200 * time.Millisecond)
	recs, err := st.UnreportedCommands(context.Background())
	if err != nil || len(recs) != 1 || recs[0].State != protocol.StateSucceeded {
		t.Fatalf("estado final deveria aguardar na base local: %+v %v", recs, err)
	}
	srv.fail.Store(false)
	e.Kick()
	if u := waitFinal(t, srv, "k"); u.State != protocol.StateSucceeded {
		t.Fatalf("%+v", u)
	}

	// Simula reinício: comandos gravados como em execução são finalizados na recuperação.
	for _, id := range []string{"t1", "r1"} {
		typ := "travado"
		if id == "r1" {
			typ = "reconnect"
		}
		raw, _ := json.Marshal(protocol.CommandUpdate{ID: id, State: protocol.StateRunning})
		if err := st.PutCommandState(context.Background(), id, typ, protocol.StateRunning, raw, time.Now()); err != nil {
			t.Fatal(err)
		}
	}
	if err := e.Recover(context.Background()); err != nil {
		t.Fatal(err)
	}
	e.Kick()
	if u := waitFinal(t, srv, "t1"); u.State != protocol.StateFailed || !strings.Contains(u.Error, "reiniciado") {
		t.Fatalf("%+v", u)
	}
	if u := waitFinal(t, srv, "r1"); u.State != protocol.StateSucceeded {
		t.Fatalf("reconnect interrompido pelo reinício = reconexão feita: %+v", u)
	}
	close(block)

	// Servidor recusou de vez (comando desconhecido lá): não fica reenviando para sempre.
	srv.reject.Store(true)
	e.Dispatch(context.Background(), cmd("z", "ok"))
	deadline := time.Now().Add(5 * time.Second)
	for {
		recs, _ := st.UnreportedCommands(context.Background())
		if len(recs) == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("atualizações recusadas continuam pendentes: %+v", recs)
		}
		time.Sleep(20 * time.Millisecond)
	}
	if n, err := st.PruneCommands(context.Background(), time.Now().Add(8*24*time.Hour)); err != nil || n == 0 {
		t.Fatalf("limpeza de registros antigos: %d %v", n, err)
	}
}
