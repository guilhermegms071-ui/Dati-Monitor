package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
)

// fakeServer answers the agent endpoints like the backend does (HMAC-checked tokens).
type fakeServer struct {
	key     []byte
	revoked bool

	mu         sync.Mutex
	heartbeats []protocol.HeartbeatRequest
	configs    int
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
		if f.revoked {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"detail":{"code":"agent_revoked","message":"revogado"}}`))
			return
		}
		if req.Signature != api.Sign(f.key, req.AgentID, req.Timestamp, req.Nonce) {
			t.Error("assinatura inválida")
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		now := time.Now().UTC()
		_ = json.NewEncoder(w).Encode(protocol.TokenResponse{AccessToken: "t", ExpiresAt: now.Add(15 * time.Minute), ServerTime: now})
	})
	mux.HandleFunc("POST /api/agent/heartbeat", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.HeartbeatRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
		}
		f.mu.Lock()
		f.heartbeats = append(f.heartbeats, req)
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(protocol.HeartbeatResponse{V: 1, ClusterRole: "master", ConfigVersion: 2})
	})
	mux.HandleFunc("GET /api/agent/commands/pending", func(w http.ResponseWriter, _ *http.Request) {
		f.mu.Lock()
		cmds := f.pending
		f.pending = nil
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(protocol.PendingCommandsResponse{V: 1, Commands: cmds})
	})
	mux.HandleFunc("POST /api/agent/commands/{id}/update", func(w http.ResponseWriter, r *http.Request) {
		var u protocol.CommandUpdate
		if err := json.NewDecoder(r.Body).Decode(&u); err != nil || u.ID != r.PathValue("id") {
			t.Errorf("atualização inválida: %v", err)
		}
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
		_ = json.NewEncoder(w).Encode(protocol.UploadResponse{V: 1, ID: "arquivo-1", SizeBytes: int64(len(body))})
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
	mux.HandleFunc("GET /api/agent/config", func(w http.ResponseWriter, _ *http.Request) {
		f.mu.Lock()
		f.configs++
		f.mu.Unlock()
		_ = json.NewEncoder(w).Encode(protocol.AgentConfig{
			V: 1, ConfigVersion: 2, SiteID: "s1", ClusterRole: "master", Paused: true,
			Intervals: protocol.Intervals{CountersMinutes: 30},
		})
	})
	return mux
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

func setup(t *testing.T, f *fakeServer) (string, string) {
	t.Helper()
	sec := bytes.Repeat([]byte{9}, secret.Size)
	f.key = secret.DeriveKey(sec)
	srv := httptest.NewServer(f.handler(t))
	t.Cleanup(srv.Close)
	dir := t.TempDir()
	if err := secret.Save(dir, sec); err != nil {
		t.Fatal(err)
	}
	addr := freeAddr(t)
	local := &config.Local{
		ServerURL: srv.URL, AgentID: "agent-1", HealthAddr: addr, EnrolledAt: time.Now().UTC(),
		// Configuração em cache de uma execução anterior.
		Server: &protocol.AgentConfig{ConfigVersion: 1, ClusterRole: "standby"},
	}
	if err := config.Save(dir, local); err != nil {
		t.Fatal(err)
	}
	return dir, addr
}

func quiet() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func TestAgentHeartbeatFetchesAndPersistsConfig(t *testing.T) {
	f := &fakeServer{}
	dir, addr := setup(t, f)
	a, err := New(dir, quiet())
	if err != nil {
		t.Fatal(err)
	}
	a.ContingencyAfter = 0 // o servidor falso não tem WebSocket: heartbeat pelo HTTPS desde já
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- a.Run(ctx) }()

	var rep health.Report
	deadline := time.Now().Add(15 * time.Second)
	for {
		f.mu.Lock()
		configs := f.configs
		f.mu.Unlock()
		if configs == 1 && a.applied.Load() == 2 {
			if resp, err := http.Get("http://" + addr + "/health"); err == nil {
				_ = json.NewDecoder(resp.Body).Decode(&rep)
				_ = resp.Body.Close()
				if rep.Info != nil {
					break
				}
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("agente não aplicou a configuração (configs=%d aplicada=%d)", configs, a.applied.Load())
		}
		time.Sleep(50 * time.Millisecond)
	}
	cancel()
	if err := <-done; err != nil {
		t.Fatalf("Run: %v", err)
	}

	f.mu.Lock()
	hb := f.heartbeats[0]
	f.mu.Unlock()
	if hb.AppliedConfigVersion != 1 || hb.Hostname == "" || hb.Version == "" || hb.V != protocol.Version || hb.OS == "" {
		t.Fatalf("heartbeat: %+v", hb)
	}
	if rep.Info["agent_id"] != "agent-1" || rep.Info["cluster_role"] != "master" || rep.Info["paused"] != true ||
		rep.Info["applied_config"] != float64(2) || rep.Info["last_heartbeat_at"] == nil {
		t.Fatalf("saúde: %+v", rep.Info)
	}
	saved, err := config.Load(dir)
	if err != nil || saved.Server == nil || saved.Server.ConfigVersion != 2 || saved.Server.Intervals.CountersMinutes != 30 {
		t.Fatalf("configuração persistida: %+v %v", saved, err)
	}
}

func TestRevokedAgentStopsCollecting(t *testing.T) {
	f := &fakeServer{revoked: true}
	dir, _ := setup(t, f)
	a, err := New(dir, quiet())
	if err != nil {
		t.Fatal(err)
	}
	a.ContingencyAfter = 0 // sem WebSocket no servidor falso: heartbeat pelo HTTPS
	a.Collector.SetRole("master", false)
	a.heartbeatOnce(context.Background())
	if a.Collector.Role() != "standby" || !a.Collector.Paused() {
		t.Fatal("coletor revogado deve parar de coletar")
	}
	if e, _ := a.heartbeatError.Load().(string); e == "" {
		t.Fatal("o erro do heartbeat deve ficar visível em /health")
	}
	_ = a.Store.Close()
}

func TestNewRequiresEnrollment(t *testing.T) {
	if _, err := New(t.TempDir(), quiet()); !errors.Is(err, config.ErrNotEnrolled) {
		t.Fatalf("%v", err)
	}
	dir := t.TempDir()
	if err := config.Save(dir, &config.Local{ServerURL: "https://x.example.com", AgentID: "a"}); err != nil {
		t.Fatal(err)
	}
	if _, err := New(dir, quiet()); !errors.Is(err, secret.ErrMissing) {
		t.Fatalf("sem credencial: %v", err)
	}
}

func (f *fakeServer) finalUpdate(id string) (protocol.CommandUpdate, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for i := len(f.updates) - 1; i >= 0; i-- {
		u := f.updates[i]
		if u.ID == id && (u.State == protocol.StateSucceeded || u.State == protocol.StateFailed) {
			return u, true
		}
	}
	return protocol.CommandUpdate{}, false
}

func TestCommandsOverHTTPSContingency(t *testing.T) {
	f := &fakeServer{}
	dir, _ := setup(t, f)
	if err := config.EnsureDirs(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "logs", "agent.log"), []byte(`{"msg":"teste"}`+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	a, err := New(dir, quiet())
	if err != nil {
		t.Fatal(err)
	}
	a.ContingencyAfter, a.PollInterval, a.ReconnectWait = 0, 50*time.Millisecond, 300*time.Millisecond
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = ln.Close() }()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			_ = c.Close()
		}
	}()
	open := ln.Addr().(*net.TCPAddr).Port
	now := time.Now().UTC()
	mk := func(id, typ, params string) protocol.CommandMessage {
		return protocol.CommandMessage{ID: id, Type: typ, Params: json.RawMessage(params), CreatedAt: now, ExpiresAt: now.Add(time.Hour)}
	}
	f.mu.Lock()
	f.pending = []protocol.CommandMessage{
		mk("d1", "diagnostics", `{}`),
		mk("p1", "pause", `{}`),
		mk("l1", "get_logs", `{"hours": 2}`),
		mk("w1", "wake_host", `{"mac":"02:00:5e:00:00:02","target_ips":["192.168.50.9"]}`),
		mk("s1", "scan_now", `{}`),
		mk("x1", "formatar", `{}`),
		mk("c1", "set_config", `{"config_version": 2}`),
		mk("g1", "ping_host", fmt.Sprintf(`{"ip":"127.0.0.1","ports":[%d]}`, open)),
		mk("r1", "restart_watchdog", `{}`),
		mk("k1", "reconnect", `{}`),
		mk("m1", "promote_master", `{}`),
		mk("v1", "read_device", `{"ip":"127.0.0.1","port":1}`),
		mk("t1", "snmp_test", `{"ip":"127.0.0.1"}`),
		mk("q1", "mib_walk", `{"ip":"abc"}`),
		mk("n1", "read_now", `{}`),
	}
	f.mu.Unlock()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- a.Run(ctx) }()
	defer func() { cancel(); <-done }()

	results := map[string]protocol.CommandUpdate{}
	deadline := time.Now().Add(20 * time.Second)
	ids := []string{"d1", "p1", "l1", "w1", "s1", "x1", "c1", "g1", "r1", "k1", "m1", "v1", "t1", "q1", "n1"}
	for len(results) < len(ids) {
		for _, id := range ids {
			if u, ok := f.finalUpdate(id); ok {
				results[id] = u
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("comandos não terminaram: %+v", results)
		}
		time.Sleep(50 * time.Millisecond)
	}
	d := results["d1"]
	if d.State != protocol.StateSucceeded || d.Result["clock"] == nil || d.Result["https"] == nil || d.Result["disk"] == nil {
		t.Fatalf("diagnóstico: %+v", d)
	}
	https, _ := d.Result["https"].(map[string]any)
	if https["status"] != float64(http.StatusNotFound) { // o servidor falso não tem /api/health: status registrado
		t.Fatalf("teste HTTPS: %+v", https)
	}
	if ws, _ := d.Result["websocket"].(map[string]any); ws["connected"] != false {
		t.Fatalf("websocket: %+v", ws)
	}
	if results["p1"].State != protocol.StateSucceeded || !a.Collector.Paused() {
		t.Fatalf("pausa: %+v", results["p1"])
	}
	l := results["l1"]
	f.mu.Lock()
	zipped := f.uploads["logs:l1"]
	f.mu.Unlock()
	if l.State != protocol.StateSucceeded || l.Result["log_id"] != "arquivo-1" || len(zipped) == 0 || string(zipped[:2]) != "PK" {
		t.Fatalf("logs: %+v (%d bytes)", l, len(zipped))
	}
	if w := results["w1"]; w.State != protocol.StateSucceeded || w.Result["mac"] != "02:00:5e:00:00:02" {
		t.Fatalf("wake: %+v", w)
	}
	// Coletor pausado/sem configuração não varre: falha com motivo claro.
	if s := results["s1"]; s.State != protocol.StateFailed || s.Error == "" {
		t.Fatalf("scan: %+v", s)
	}
	if x := results["x1"]; x.State != protocol.StateFailed || !strings.Contains(x.Error, "não é suportado") {
		t.Fatalf("desconhecido: %+v", x)
	}
	if c := results["c1"]; c.State != protocol.StateSucceeded || c.Result["applied_config_version"] != float64(2) {
		t.Fatalf("set_config: %+v", c)
	}
	g := results["g1"]
	if g.State != protocol.StateSucceeded || g.Result["reachable"] != true {
		t.Fatalf("ping: %+v", g)
	}
	if r := results["r1"]; r.State != protocol.StateFailed || r.Error == "" {
		t.Fatalf("watchdog ainda não instalado neste PC: %+v", r)
	}
	if k := results["k1"]; k.State != protocol.StateFailed || !strings.Contains(k.Error, "não voltou") {
		t.Fatalf("reconnect sem gateway: %+v", k)
	}
	if m := results["m1"]; m.State != protocol.StateSucceeded || m.Result["cluster_role"] != "master" {
		t.Fatalf("promote: %+v", m)
	}
	for _, id := range []string{"v1", "t1"} { // configuração do servidor falso não tem credenciais
		if r := results[id]; r.State != protocol.StateFailed || !strings.Contains(r.Error, "credencial") {
			t.Fatalf("%s: %+v", id, r)
		}
	}
	if q := results["q1"]; q.State != protocol.StateFailed || !strings.Contains(q.Error, "IP inválido") {
		t.Fatalf("walk: %+v", q)
	}
	if n := results["n1"]; n.State != protocol.StateFailed || n.Error == "" {
		t.Fatalf("read_now pausado: %+v", n)
	}
}
