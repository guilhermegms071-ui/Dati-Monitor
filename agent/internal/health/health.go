// Package health tracks the "heartbeat" of each internal loop and serves GET /health on
// 127.0.0.1:47701 (PROMPT 4.1): a loop that has not beaten for more than 3× its interval makes the
// agent unhealthy (503), which the watchdog uses to restart it.
package health

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"sort"
	"sync"
	"time"
)

// DefaultAddr is where the health endpoint listens (loopback only).
const DefaultAddr = "127.0.0.1:47701"

// Registry holds the loops and extra info.
type Registry struct {
	mu        sync.Mutex
	loops     map[string]*loop
	startedAt time.Time
	info      func() map[string]any
	now       func() time.Time
}

type loop struct {
	interval time.Duration
	last     time.Time
}

// NewRegistry creates an empty registry.
func NewRegistry() *Registry {
	return &Registry{loops: map[string]*loop{}, startedAt: time.Now(), now: time.Now}
}

// SetInfo sets a function returning extra fields for the report (queue size, last scan...).
func (r *Registry) SetInfo(fn func() map[string]any) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.info = fn
}

// Register declares a loop and its expected beat interval (counts as beaten now).
func (r *Registry) Register(name string, interval time.Duration) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.loops[name] = &loop{interval: interval, last: r.now()}
}

// Beat records that the loop is alive.
func (r *Registry) Beat(name string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if l, ok := r.loops[name]; ok {
		l.last = r.now()
	}
}

// LoopReport is the state of one loop.
type LoopReport struct {
	LastBeat        time.Time `json:"last_beat"`
	IntervalSeconds float64   `json:"interval_seconds"`
	Healthy         bool      `json:"healthy"`
}

// Report is the /health body.
type Report struct {
	Status        string                `json:"status"` // ok | unhealthy
	UptimeSeconds int64                 `json:"uptime_seconds"`
	Loops         map[string]LoopReport `json:"loops"`
	Unhealthy     []string              `json:"unhealthy,omitempty"`
	Info          map[string]any        `json:"info,omitempty"`
}

// Snapshot computes the current report.
func (r *Registry) Snapshot() Report {
	r.mu.Lock()
	now := r.now()
	rep := Report{Status: "ok", UptimeSeconds: int64(now.Sub(r.startedAt).Seconds()), Loops: map[string]LoopReport{}}
	for name, l := range r.loops {
		healthy := now.Sub(l.last) <= 3*l.interval
		rep.Loops[name] = LoopReport{LastBeat: l.last.UTC(), IntervalSeconds: l.interval.Seconds(), Healthy: healthy}
		if !healthy {
			rep.Unhealthy = append(rep.Unhealthy, name)
		}
	}
	info := r.info
	r.mu.Unlock()
	sort.Strings(rep.Unhealthy)
	if len(rep.Unhealthy) > 0 {
		rep.Status = "unhealthy"
	}
	if info != nil {
		rep.Info = info()
	}
	return rep
}

// Handler serves the report: 200 when healthy, 503 otherwise.
func (r *Registry) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.URL.Path != "/health" {
			http.NotFound(w, req)
			return
		}
		rep := r.Snapshot()
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		if rep.Status != "ok" {
			w.WriteHeader(http.StatusServiceUnavailable)
		}
		_ = json.NewEncoder(w).Encode(rep)
	})
}

// Serve listens on addr (must be loopback) until ctx ends.
func Serve(ctx context.Context, addr string, r *Registry) error {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return err
	}
	if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
		return errors.New("o endpoint de saúde só pode escutar em 127.0.0.1")
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	srv := &http.Server{Handler: r.Handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() { //nolint:gosec // G118: o ctx já foi cancelado; o desligamento precisa de um prazo próprio
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = srv.Shutdown(sctx)
	}()
	if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
