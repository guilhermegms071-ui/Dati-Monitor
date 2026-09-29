package watchdog

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/release"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// ----------------------------------------------------------------------------- servidor falso

type fakeServer struct {
	key []byte

	mu         sync.Mutex
	heartbeats []protocol.WatchdogHeartbeatRequest
	pending    []protocol.CommandMessage
	updates    []protocol.CommandUpdate
	uploads    map[string][]byte
	releases   map[string][]byte
}

func (f *fakeServer) handler(t *testing.T) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/agent/token", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.TokenRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req.Signature != api.Sign(f.key, req.AgentID, req.Timestamp, req.Nonce) {
			t.Error("assinatura inválida")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		now := time.Now().UTC()
		_ = json.NewEncoder(w).Encode(protocol.TokenResponse{AccessToken: "t", ExpiresAt: now.Add(15 * time.Minute), ServerTime: now})
	})
	mux.HandleFunc("POST /api/watchdog/heartbeat", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.WatchdogHeartbeatRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
		}
		f.mu.Lock()
		f.heartbeats = append(f.heartbeats, req)
		cmds := f.pending
		f.pending = nil
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(protocol.WatchdogHeartbeatResponse{V: 1, ServerTime: time.Now().UTC(), Commands: cmds})
	})
	mux.HandleFunc("POST /api/agent/commands/{id}/update", func(w http.ResponseWriter, r *http.Request) {
		var u protocol.CommandUpdate
		_ = json.NewDecoder(r.Body).Decode(&u)
		f.mu.Lock()
		f.updates = append(f.updates, u)
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(protocol.CommandUpdateResponse{V: 1, ID: u.ID, State: u.State})
	})
	mux.HandleFunc("POST /api/agent/uploads/{kind}", func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		if f.uploads == nil {
			f.uploads = map[string][]byte{}
		}
		f.uploads[r.PathValue("kind")+":"+r.URL.Query().Get("command_id")] = body
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(protocol.UploadResponse{V: 1, ID: "log-1", SizeBytes: int64(len(body))})
	})
	mux.HandleFunc("GET /api/agent/releases/{id}/file", func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		data, ok := f.releases[r.PathValue("id")]
		f.mu.Unlock()
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_, _ = w.Write(data)
	})
	return mux
}

func (f *fakeServer) push(cmds ...protocol.CommandMessage) {
	f.mu.Lock()
	f.pending = append(f.pending, cmds...)
	f.mu.Unlock()
}

func (f *fakeServer) finals() map[string]protocol.CommandUpdate {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := map[string]protocol.CommandUpdate{}
	for _, u := range f.updates {
		if u.State == protocol.StateSucceeded || u.State == protocol.StateFailed {
			out[u.ID] = u
		}
	}
	return out
}

func (f *fakeServer) restartReasons() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for _, hb := range f.heartbeats {
		for _, r := range hb.Restarts {
			out = append(out, r.Reason)
		}
	}
	return out
}

// ----------------------------------------------------------------------------- coletor falso

// fakeTarget is the agent "service"; its binary is a text file with the version it runs.
type fakeTarget struct {
	exe string

	mu          sync.Mutex
	state       string
	pid         int
	starts      int
	uninstalled bool
}

func (f *fakeTarget) Status(context.Context) (svc.Status, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return svc.Status{State: f.state, PID: f.pid, Exe: f.exe}, nil
}

func (f *fakeTarget) Start(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.state = svc.StateRunning
	f.starts++
	return nil
}

func (f *fakeTarget) Stop(context.Context) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.state = svc.StateStopped
	return nil
}

func (f *fakeTarget) Exe() (string, error) { return f.exe, nil }

func (f *fakeTarget) Uninstall(ctx context.Context) error {
	_ = f.Stop(ctx)
	f.mu.Lock()
	f.uninstalled = true
	f.mu.Unlock()
	return nil
}

func (f *fakeTarget) running() (bool, int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.state == svc.StateRunning, f.starts
}

func (f *fakeTarget) version(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(f.exe)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func freeAddr(t *testing.T) string {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = ln.Close() }()
	return "127.0.0.1:" + strconv.Itoa(ln.Addr().(*net.TCPAddr).Port)
}

// agentHealth serves the fake agent's /health: healthy while running and not "broken".
func agentHealth(t *testing.T, target *fakeTarget, sick *atomic.Bool) string {
	t.Helper()
	addr := freeAddr(t)
	srv := &http.Server{ReadHeaderTimeout: time.Second, Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		running, _ := target.running()
		v := target.version(t)
		rep := health.Report{Status: "ok", Info: map[string]any{
			"version": v, "last_heartbeat_at": time.Now().UTC().Format(time.RFC3339Nano),
		}}
		if !running || v == "broken" || sick.Load() {
			rep.Status, rep.Unhealthy = "unhealthy", []string{"heartbeat"}
			w.WriteHeader(http.StatusServiceUnavailable)
		}
		_ = json.NewEncoder(w).Encode(rep)
	})}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return addr
}

type harness struct {
	server   *fakeServer
	target   *fakeTarget
	sick     *atomic.Bool
	priv     ed25519.PrivateKey
	w        *Watchdog
	cancel   context.CancelFunc
	done     chan struct{}
	uninstal atomic.Bool
	dataDir  string
}

func start(t *testing.T, state string, tweak func(*Options)) *harness {
	t.Helper()
	sec := bytes.Repeat([]byte{7}, secret.Size)
	f := &fakeServer{key: secret.DeriveKey(sec), releases: map[string][]byte{}}
	srv := httptest.NewServer(f.handler(t))
	t.Cleanup(srv.Close)
	client, err := api.New(api.Options{ServerURL: srv.URL}, "agent-1", f.key)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	exe := filepath.Join(dir, "bin", "dm-agent.exe")
	if err := os.MkdirAll(filepath.Dir(exe), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(exe, []byte("1.0.0"), 0o600); err != nil {
		t.Fatal(err)
	}
	target := &fakeTarget{exe: exe, state: state}
	h := &harness{server: f, target: target, sick: &atomic.Bool{}, dataDir: dir}
	_, h.priv, _ = ed25519.GenerateKey(nil)
	o := Options{
		DataDir: dir, AgentHealth: agentHealth(t, target, h.sick), Client: client, Target: target,
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Version: "1.0.9",
		PublicKey:  h.priv.Public().(ed25519.PublicKey),
		CheckEvery: 40 * time.Millisecond, ReportEvery: 80 * time.Millisecond,
		StartGrace: time.Millisecond, UpdateWait: 3 * time.Second,
		OnUninstall: func() { h.uninstal.Store(true) },
	}
	if tweak != nil {
		tweak(&o)
	}
	h.w, err = New(o)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	h.cancel, h.done = cancel, make(chan struct{})
	go func() { _ = h.w.Run(ctx); close(h.done) }()
	t.Cleanup(func() { cancel(); <-h.done })
	return h
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("tempo esgotado esperando: %s", what)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func hasReason(reasons []string, part string) bool {
	for _, r := range reasons {
		if strings.Contains(r, part) {
			return true
		}
	}
	return false
}

// ----------------------------------------------------------------------------- testes

func TestStoppedAgentIsStartedAndTheReasonReported(t *testing.T) {
	h := start(t, svc.StateStopped, nil)
	waitFor(t, "motivo no heartbeat", func() bool { return hasReason(h.server.restartReasons(), "estava parado") })
	if running, _ := h.target.running(); !running {
		t.Fatal("coletor não foi iniciado")
	}
	h.server.mu.Lock()
	last := h.server.heartbeats[len(h.server.heartbeats)-1]
	h.server.mu.Unlock()
	if last.Version != "1.0.9" || last.OS != runtime.GOOS || last.Arch != runtime.GOARCH {
		t.Fatalf("heartbeat: %+v", last)
	}
	// Motivo enviado uma vez só (não repete em todo heartbeat).
	waitFor(t, "mais heartbeats", func() bool {
		h.server.mu.Lock()
		defer h.server.mu.Unlock()
		return len(h.server.heartbeats) >= 4
	})
	if n := len(h.server.restartReasons()); n != 1 {
		t.Fatalf("motivos repetidos: %v", h.server.restartReasons())
	}
}

func TestHealthFailingThreeTimesRestarts(t *testing.T) {
	h := start(t, svc.StateRunning, nil)
	waitFor(t, "heartbeat saudável", func() bool {
		h.server.mu.Lock()
		defer h.server.mu.Unlock()
		for _, hb := range h.server.heartbeats {
			if hb.AgentHealthy && hb.AgentVersion == "1.0.0" {
				return true
			}
		}
		return false
	})
	h.sick.Store(true)
	waitFor(t, "reinício por /health", func() bool { return hasReason(h.server.restartReasons(), "/health falhou 3 vezes") })
}

func TestMemoryAboveLimitRestarts(t *testing.T) {
	h := start(t, svc.StateRunning, func(o *Options) { o.MemoryLimit = 1024 })
	h.target.mu.Lock()
	h.target.pid = os.Getpid() // memória real deste processo, muito acima de 1 KB
	h.target.mu.Unlock()
	waitFor(t, "reinício por memória", func() bool { return hasReason(h.server.restartReasons(), "memória acima de") })
}

func cmd(id, typ string, params any) protocol.CommandMessage {
	raw, _ := json.Marshal(params)
	now := time.Now().UTC()
	return protocol.CommandMessage{V: 1, ID: id, Type: typ, Params: raw, CreatedAt: now, ExpiresAt: now.Add(10 * time.Minute)}
}

func (h *harness) release(id, version, content string) protocol.UpdateParams {
	data := []byte(content)
	sig, digest := release.Sign(h.priv, "agent", version, runtime.GOOS, runtime.GOARCH, data)
	h.server.mu.Lock()
	h.server.releases[id] = data
	h.server.mu.Unlock()
	return protocol.UpdateParams{
		ReleaseID: id, Component: "agent", Version: version, OS: runtime.GOOS, Arch: runtime.GOARCH,
		SHA256: digest, Signature: sig, SizeBytes: int64(len(data)), URL: "/api/agent/releases/" + id + "/file",
	}
}

func TestCommandsRestartUpdateLogsAndUninstall(t *testing.T) {
	h := start(t, svc.StateRunning, nil)
	if err := os.MkdirAll(filepath.Join(h.dataDir, "logs"), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(h.dataDir, "logs", "watchdog.log"), []byte(`{"msg":"ok"}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	_, startsBefore := h.target.running()
	h.server.push(cmd("c-restart", "restart_agent", map[string]any{}))
	waitFor(t, "restart_agent concluído", func() bool { return h.server.finals()["c-restart"].State == protocol.StateSucceeded })
	_, starts := h.target.running()
	if starts != startsBefore+1 {
		t.Fatalf("partidas: %d → %d", startsBefore, starts)
	}
	// Reentrega do mesmo comando: não executa de novo, só reenvia o resultado.
	h.server.push(cmd("c-restart", "restart_agent", map[string]any{}))
	waitFor(t, "resultado reenviado", func() bool {
		h.server.mu.Lock()
		defer h.server.mu.Unlock()
		n := 0
		for _, u := range h.server.updates {
			if u.ID == "c-restart" && u.State == protocol.StateSucceeded {
				n++
			}
		}
		return n == 2
	})
	if _, again := h.target.running(); again != starts {
		t.Fatal("comando repetido foi executado duas vezes")
	}

	h.server.push(cmd("c-update", "update", h.release("r-110", "1.1.0", "1.1.0")))
	waitFor(t, "update concluído", func() bool { return h.server.finals()["c-update"].State != "" })
	if u := h.server.finals()["c-update"]; u.State != protocol.StateSucceeded || u.Result["to"] != "1.1.0" {
		t.Fatalf("update: %+v", u)
	}
	if h.target.version(t) != "1.1.0" {
		t.Fatalf("binário: %s", h.target.version(t))
	}
	waitFor(t, "versão anterior no heartbeat", func() bool {
		h.server.mu.Lock()
		defer h.server.mu.Unlock()
		last := h.server.heartbeats[len(h.server.heartbeats)-1]
		return last.PreviousAgentVersion == "1.0.0" && last.AgentVersion == "1.1.0"
	})

	h.server.push(cmd("c-rollback", "rollback", map[string]any{}))
	waitFor(t, "rollback concluído", func() bool { return h.server.finals()["c-rollback"].State == protocol.StateSucceeded })
	if h.target.version(t) != "1.0.0" {
		t.Fatalf("depois do rollback: %s", h.target.version(t))
	}

	h.server.push(cmd("c-logs", "get_logs", map[string]any{"hours": 1, "source": "watchdog"}))
	waitFor(t, "logs enviados", func() bool { return h.server.finals()["c-logs"].State == protocol.StateSucceeded })
	h.server.mu.Lock()
	zipped := h.server.uploads["logs:c-logs"]
	h.server.mu.Unlock()
	if !bytes.HasPrefix(zipped, []byte("PK")) {
		t.Fatalf("upload não é zip: %q", zipped[:min(10, len(zipped))])
	}

	h.server.push(cmd("c-bad", "scan_now", map[string]any{}))
	waitFor(t, "comando do coletor recusado", func() bool { return h.server.finals()["c-bad"].State == protocol.StateFailed })

	h.server.push(cmd("c-uninstall", "uninstall", map[string]any{"confirm_name": "PC"}))
	waitFor(t, "desinstalado", func() bool { return h.uninstal.Load() })
	if !h.target.uninstalled || h.server.finals()["c-uninstall"].State != protocol.StateSucceeded {
		t.Fatal("desinstalação não concluída/relatada antes de encerrar")
	}
}

func TestBrokenVersionIsRolledBackAndReportedFailed(t *testing.T) {
	h := start(t, svc.StateRunning, nil)
	h.server.push(cmd("c-bad-update", "update", h.release("r-bad", "1.2.0", "broken")))
	waitFor(t, "update com falha", func() bool { return h.server.finals()["c-bad-update"].State != "" })
	u := h.server.finals()["c-bad-update"]
	if u.State != protocol.StateFailed || !strings.Contains(u.Error, "rollback para 1.0.0 feito") {
		t.Fatalf("update ruim: %+v", u)
	}
	if u.Result["rolled_back"] != true || h.target.version(t) != "1.0.0" {
		t.Fatalf("rollback não restaurou: %+v %s", u.Result, h.target.version(t))
	}

	// Binário adulterado no caminho: recusado antes de parar o coletor.
	p := h.release("r-tampered", "1.3.0", "1.3.0")
	h.server.mu.Lock()
	h.server.releases["r-tampered"] = []byte("1.3.X")
	h.server.mu.Unlock()
	_, startsBefore := h.target.running()
	h.server.push(cmd("c-tampered", "update", p))
	waitFor(t, "update adulterado recusado", func() bool { return h.server.finals()["c-tampered"].State == protocol.StateFailed })
	if _, starts := h.target.running(); starts != startsBefore {
		t.Fatal("o coletor foi parado por um binário adulterado")
	}
}
