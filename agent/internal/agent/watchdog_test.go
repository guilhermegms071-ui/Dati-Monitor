package agent

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/release"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// fakeWatchdogService is the dm-watchdog service seen by the agent; its binary is a text file with the
// version it runs.
type fakeWatchdogService struct {
	exe string

	mu        sync.Mutex
	state     string
	statusErr error
	starts    int
}

func (f *fakeWatchdogService) Status(context.Context) (svc.Status, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return svc.Status{State: f.state, Exe: f.exe}, f.statusErr
}

func (f *fakeWatchdogService) Start(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.state = svc.StateRunning
	f.starts++
	return nil
}

func (f *fakeWatchdogService) Stop(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.state = svc.StateStopped
	return nil
}

func (f *fakeWatchdogService) Exe() (string, error)            { return f.exe, nil }
func (f *fakeWatchdogService) Uninstall(context.Context) error { return errors.New("não usado") }

func TestMutualWatchStartsAStoppedWatchdog(t *testing.T) {
	f := &fakeServer{}
	dir, _ := setup(t, f)
	a, err := New(dir, quiet())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = a.Store.Close() })
	wd := &fakeWatchdogService{state: svc.StateStopped}
	a.WatchdogService = wd
	ctx := context.Background()

	a.checkWatchdog(ctx)
	if wd.starts != 1 || a.WatchdogState() != protocol.ServiceStarting {
		t.Fatalf("watchdog parado: partidas=%d estado=%s", wd.starts, a.WatchdogState())
	}
	a.checkWatchdog(ctx)
	if wd.starts != 1 || a.WatchdogState() != protocol.ServiceRunning {
		t.Fatalf("watchdog rodando: partidas=%d estado=%s", wd.starts, a.WatchdogState())
	}
	if hb := a.HeartbeatRequest(ctx); hb.WatchdogState != protocol.ServiceRunning {
		t.Fatalf("heartbeat sem o estado do watchdog: %q", hb.WatchdogState)
	}

	wd.mu.Lock()
	wd.state = svc.StateNotInstalled
	wd.mu.Unlock()
	a.checkWatchdog(ctx)
	if a.WatchdogState() != protocol.ServiceNotInstalled || wd.starts != 1 {
		t.Fatalf("não instalado: %s", a.WatchdogState())
	}
	wd.mu.Lock()
	wd.statusErr = errors.New("sem acesso ao SCM")
	wd.mu.Unlock()
	a.checkWatchdog(ctx)
	if a.WatchdogState() != protocol.ServiceUnknown {
		t.Fatalf("erro de consulta: %s", a.WatchdogState())
	}
}

// watchdogHealth serves a fake watchdog /health reporting the version in the service's binary.
func watchdogHealth(t *testing.T, wd *fakeWatchdogService) string {
	t.Helper()
	addr := freeAddr(t)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	srv := &http.Server{ReadHeaderTimeout: time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		raw, _ := os.ReadFile(wd.exe)
		st, _ := wd.Status(context.Background())
		rep := health.Report{Status: "ok", Info: map[string]any{
			"version": string(raw), "last_report_at": time.Now().UTC().Format(time.RFC3339Nano),
		}}
		if st.State != svc.StateRunning || string(raw) == "broken" {
			rep.Status = "unhealthy"
			rep.Info["last_report_at"] = nil
		}
		_ = json.NewEncoder(w).Encode(rep)
	})}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return addr
}

func TestAgentUpdatesTheWatchdog(t *testing.T) {
	_, priv, _ := ed25519.GenerateKey(nil)
	data := []byte("1.0.4")
	sig, digest := release.Sign(priv, "watchdog", "1.0.4", runtime.GOOS, runtime.GOARCH, data)
	f := &fakeServer{releases: map[string][]byte{"wd-104": data}}
	dir, _ := setup(t, f)
	a, err := New(dir, quiet())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = a.Store.Close() })
	exe := filepath.Join(t.TempDir(), "dm-watchdog.exe")
	if err := os.WriteFile(exe, []byte("1.0.3"), 0o600); err != nil {
		t.Fatal(err)
	}
	wd := &fakeWatchdogService{exe: exe, state: svc.StateRunning}
	a.WatchdogService, a.WatchdogHealth, a.ReleaseKey = wd, watchdogHealth(t, wd), priv.Public().(ed25519.PublicKey)

	params := protocol.UpdateParams{
		ReleaseID: "wd-104", Component: "watchdog", Version: "1.0.4", OS: runtime.GOOS, Arch: runtime.GOARCH,
		SHA256: digest, Signature: sig, SizeBytes: int64(len(data)), URL: "/api/agent/releases/wd-104/file",
	}
	raw, _ := json.Marshal(params)
	var steps []string
	res, err := a.cmdUpdate(context.Background(), protocol.CommandMessage{ID: "c1", Type: "update", Params: raw},
		func(s string) { steps = append(steps, s) })
	if err != nil {
		t.Fatal(err)
	}
	if res.Data["from"] != "1.0.3" || res.Data["to"] != "1.0.4" {
		t.Fatalf("resultado: %+v", res.Data)
	}
	if got, _ := os.ReadFile(exe); string(got) != "1.0.4" || len(steps) != 1 {
		t.Fatalf("binário do watchdog: %s passos=%v", got, steps)
	}

	params.Component = "agent"
	raw, _ = json.Marshal(params)
	_, err = a.cmdUpdate(context.Background(), protocol.CommandMessage{ID: "c2", Type: "update", Params: raw}, func(string) {})
	if err == nil || !strings.Contains(err.Error(), "atualiza só o watchdog") {
		t.Fatalf("update do próprio coletor aceito: %v", err)
	}
}
