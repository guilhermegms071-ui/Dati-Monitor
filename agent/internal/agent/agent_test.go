package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
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
