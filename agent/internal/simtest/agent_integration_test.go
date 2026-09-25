//go:build integration

package simtest

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/agent"
	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// fakeAPI plays the server side of the protocol (HTTPS channel only) for a real agent process.
type fakeAPI struct {
	t       *testing.T
	key     []byte
	cfg     protocol.AgentConfig
	mu      sync.Mutex
	pending []protocol.CommandMessage
	updates map[string]protocol.CommandUpdate
	items   []protocol.Item
	walks   map[string][]byte
}

func (f *fakeAPI) handler() http.Handler {
	mux := http.NewServeMux()
	write := func(w http.ResponseWriter, v any) { _ = json.NewEncoder(w).Encode(v) }
	mux.HandleFunc("POST /api/agent/token", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.TokenRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req.Signature != api.Sign(f.key, req.AgentID, req.Timestamp, req.Nonce) {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		now := time.Now().UTC()
		write(w, protocol.TokenResponse{AccessToken: "t", ExpiresAt: now.Add(15 * time.Minute), ServerTime: now})
	})
	mux.HandleFunc("POST /api/agent/heartbeat", func(w http.ResponseWriter, _ *http.Request) {
		write(w, protocol.HeartbeatResponse{ServerTime: time.Now().UTC(), ClusterRole: "master", ConfigVersion: f.cfg.ConfigVersion})
	})
	mux.HandleFunc("GET /api/agent/config", func(w http.ResponseWriter, _ *http.Request) { write(w, f.cfg) })
	mux.HandleFunc("POST /api/agent/readings", func(w http.ResponseWriter, r *http.Request) {
		zr, err := gzip.NewReader(r.Body)
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		var req protocol.ReadingsRequest
		_ = json.NewDecoder(zr).Decode(&req)
		res := protocol.ReadingsResponse{V: 1}
		f.mu.Lock()
		for _, it := range req.Items {
			f.items = append(f.items, it)
			res.Results = append(res.Results, protocol.ItemResult{Key: it.Key, Status: protocol.ResultAccepted})
		}
		f.mu.Unlock()
		write(w, res)
	})
	mux.HandleFunc("GET /api/agent/commands/pending", func(w http.ResponseWriter, _ *http.Request) {
		f.mu.Lock()
		cmds := f.pending
		f.pending = nil
		f.mu.Unlock()
		write(w, protocol.PendingCommandsResponse{V: 1, Commands: cmds})
	})
	mux.HandleFunc("POST /api/agent/commands/{id}/update", func(w http.ResponseWriter, r *http.Request) {
		var u protocol.CommandUpdate
		_ = json.NewDecoder(r.Body).Decode(&u)
		f.mu.Lock()
		if u.State == protocol.StateSucceeded || u.State == protocol.StateFailed {
			f.updates[u.ID] = u
		}
		f.mu.Unlock()
		write(w, protocol.CommandUpdateResponse{V: 1, ID: u.ID, State: u.State})
	})
	mux.HandleFunc("POST /api/agent/uploads/mib-walk", func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.walks[r.URL.Query().Get("command_id")] = body
		f.mu.Unlock()
		write(w, protocol.UploadResponse{V: 1, ID: "walk-1", SizeBytes: int64(len(body))})
	})
	return mux
}

func (f *fakeAPI) result(id string) (protocol.CommandUpdate, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	u, ok := f.updates[id]
	return u, ok
}

// TestAgentCommandsAgainstSimulator runs the real agent (contingency channel) against two simulated
// printers and exercises the SNMP commands end to end.
func TestAgentCommandsAgainstSimulator(t *testing.T) {
	konica := Start(t, "03-konica-cor")
	canon := Start(t, "01-canon-cor")
	profiles, err := profile.LoadDir(filepath.Join(RepoRoot(t), "profiles"))
	if err != nil {
		t.Fatal(err)
	}
	var raws []json.RawMessage
	for _, p := range profiles {
		raw, _ := json.Marshal(p)
		raws = append(raws, raw)
	}
	sec := bytes.Repeat([]byte{3}, secret.Size)
	f := &fakeAPI{t: t, key: secret.DeriveKey(sec), updates: map[string]protocol.CommandUpdate{}, walks: map[string][]byte{}}
	f.cfg = protocol.AgentConfig{
		V: 1, ConfigVersion: 1, SiteID: "s1", ClusterRole: "master",
		Discovery:   protocol.DiscoveryConfig{Concurrency: 8, RatePPS: 200, TimeoutMS: 1500, Retries: 1},
		Ranges:      []protocol.IPRange{{ID: "r1", CIDR: "127.0.0.1/32", Ports: []int{konica, canon}}},
		Credentials: []snmp.Credential{{ID: "c-errada", Version: "v2c", Community: "nao-e-esta"}, Public},
		Profiles:    raws,
	}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()
	dir := t.TempDir()
	if err := secret.Save(dir, sec); err != nil {
		t.Fatal(err)
	}
	if err := config.EnsureDirs(dir); err != nil {
		t.Fatal(err)
	}
	if err := config.Save(dir, &config.Local{ServerURL: srv.URL, AgentID: "a1", HealthAddr: "127.0.0.1:0"}); err != nil {
		t.Fatal(err)
	}
	a, err := agent.New(dir, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	a.ContingencyAfter, a.PollInterval = 0, 100*time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- a.Run(ctx) }()
	defer func() { cancel(); <-done }()

	// Espera a configuração ser aplicada e a primeira varredura terminar.
	deadline := time.Now().Add(60 * time.Second)
	for a.Collector.KnownDevices(ctx) < 2 {
		if time.Now().After(deadline) {
			t.Fatal("varredura inicial não encontrou as 2 impressoras")
		}
		time.Sleep(200 * time.Millisecond)
	}
	now := time.Now().UTC()
	mk := func(id, typ, params string) protocol.CommandMessage {
		return protocol.CommandMessage{ID: id, Type: typ, Params: json.RawMessage(params), CreatedAt: now, ExpiresAt: now.Add(time.Hour)}
	}
	f.mu.Lock()
	f.pending = []protocol.CommandMessage{
		mk("scan", "scan_now", `{"range_id":"r1"}`),
		mk("read", "read_now", `{"devices":[{"serial":"A797019500624"}]}`),
		mk("raw", "read_device", fmt.Sprintf(`{"ip":"127.0.0.1","port":%d}`, konica)),
		mk("test", "snmp_test", fmt.Sprintf(`{"ip":"127.0.0.1","port":%d}`, canon)),
		mk("walk", "mib_walk", fmt.Sprintf(`{"ip":"127.0.0.1","port":%d,"root_oid":"1.3.6.1.2.1.43"}`, konica)),
	}
	f.mu.Unlock()
	results := map[string]protocol.CommandUpdate{}
	deadline = time.Now().Add(90 * time.Second)
	for len(results) < 5 {
		for _, id := range []string{"scan", "read", "raw", "test", "walk"} {
			if u, ok := f.result(id); ok {
				results[id] = u
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("comandos não terminaram: %v", results)
		}
		time.Sleep(200 * time.Millisecond)
	}
	for id, u := range results {
		if u.State != protocol.StateSucceeded {
			t.Fatalf("%s falhou: %s", id, u.Error)
		}
	}
	if s := results["scan"].Result; s["printers_found"] != float64(2) || s["ranges"] != float64(1) {
		t.Fatalf("scan_now: %+v", s)
	}
	if r := results["read"].Result; r["ok"] != float64(1) || r["requested"] != float64(1) {
		t.Fatalf("read_now: %+v", r)
	}
	raw := results["raw"].Result
	counters, _ := raw["counters"].(map[string]any)
	if counters["total"] != float64(217031) || raw["profile"] != "konica-minolta" || raw["credential_id"] != "public" {
		t.Fatalf("read_device: %+v", raw)
	}
	test := results["test"].Result
	if test["answered"] != float64(1) {
		t.Fatalf("snmp_test: %+v", test)
	}
	walk := results["walk"].Result
	f.mu.Lock()
	gz := f.walks["walk"]
	f.mu.Unlock()
	zr, err := gzip.NewReader(bytes.NewReader(gz))
	if err != nil {
		t.Fatalf("walk enviado não é gzip: %v", err)
	}
	body, _ := io.ReadAll(zr)
	lines := strings.Count(strings.TrimSpace(string(body)), "\n") + 1
	if walk["walk_id"] != "walk-1" || walk["oids"] != float64(lines) || !strings.HasPrefix(string(body), "1.3.6.1.2.1.43.") {
		t.Fatalf("mib_walk: %+v (%d linhas)", walk, lines)
	}
	// A leitura sob demanda chegou ao servidor como leitura normal.
	f.mu.Lock()
	var konicaReadings int
	for _, it := range f.items {
		if it.Kind == protocol.KindReading && it.Device.Serial == "A797019500624" {
			konicaReadings++
		}
	}
	f.mu.Unlock()
	if konicaReadings < 2 { // agendada + sob demanda
		t.Fatalf("leituras da Konica recebidas: %d", konicaReadings)
	}
}
