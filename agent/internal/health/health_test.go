package health

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"testing"
	"time"
)

func TestLoopBecomesUnhealthyAfterThreeIntervals(t *testing.T) {
	r := NewRegistry()
	now := time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC)
	r.now = func() time.Time { return now }
	r.startedAt = now
	r.Register("heartbeat", 30*time.Second)
	r.Register("collector", 5*time.Second)
	r.SetInfo(func() map[string]any { return map[string]any{"queue_pending": 3} })
	r.Beat("desconhecido") // ignorado

	now = now.Add(15 * time.Second)
	r.Beat("collector")
	rep := r.Snapshot()
	if rep.Status != "ok" || rep.UptimeSeconds != 15 || rep.Info["queue_pending"] != 3 || len(rep.Loops) != 2 {
		t.Fatalf("%+v", rep)
	}
	now = now.Add(16 * time.Second) // collector: 16 s > 3×5 s
	rep = r.Snapshot()
	if rep.Status != "unhealthy" || len(rep.Unhealthy) != 1 || rep.Unhealthy[0] != "collector" || rep.Loops["heartbeat"].Healthy != true {
		t.Fatalf("%+v", rep)
	}

	rec := httptest.NewRecorder()
	r.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("código %d", rec.Code)
	}
	var body Report
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil || body.Status != "unhealthy" {
		t.Fatalf("%s %v", rec.Body.String(), err)
	}
	r.Beat("collector")
	rec = httptest.NewRecorder()
	r.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("código %d", rec.Code)
	}
	rec = httptest.NewRecorder()
	r.Handler().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/outra", nil))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("código %d", rec.Code)
	}
}

func TestServeOnlyOnLoopback(t *testing.T) {
	ctx := context.Background()
	for _, addr := range []string{"0.0.0.0:47701", "10.0.0.1:47701", "sem-porta"} {
		if err := Serve(ctx, addr, NewRegistry()); err == nil {
			t.Errorf("%s deveria ser recusado", addr)
		}
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := ln.Addr().(*net.TCPAddr).Port
	_ = ln.Close()
	addr := "127.0.0.1:" + strconv.Itoa(port)

	ctx, cancel := context.WithCancel(ctx)
	errc := make(chan error, 1)
	r := NewRegistry()
	go func() { errc <- Serve(ctx, addr, r) }()
	var resp *http.Response
	for range 50 {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+addr+"/health", nil)
		if resp, err = http.DefaultClient.Do(req); err == nil {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("código %d", resp.StatusCode)
	}
	// Porta ocupada: erro claro em vez de falha silenciosa.
	if err := Serve(context.Background(), addr, r); err == nil {
		t.Fatal("porta em uso deveria falhar")
	}
	cancel()
	if err := <-errc; err != nil {
		t.Fatalf("encerramento: %v", err)
	}
}
