package api

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

func TestNewHTTPRequiresHTTPSOutsideLoopback(t *testing.T) {
	ok := []Options{
		{ServerURL: "https://monitor.example.com"},
		{ServerURL: "http://127.0.0.1:8000"},
		{ServerURL: "http://localhost:8000/"},
		{ServerURL: "http://[::1]:8000"},
		{ServerURL: "http://10.0.0.5:8000", InsecureDev: true},
	}
	for _, o := range ok {
		if _, _, err := NewHTTP(o); err != nil {
			t.Errorf("%+v: %v", o, err)
		}
	}
	bad := []Options{
		{ServerURL: "http://10.0.0.5:8000"},
		{ServerURL: "ftp://monitor.example.com"},
		{ServerURL: "monitor.example.com"},
		{ServerURL: "https://monitor.example.com", ProxyURL: "::"},
	}
	for _, o := range bad {
		if _, _, err := NewHTTP(o); err == nil {
			t.Errorf("%+v deveria falhar", o)
		}
	}
}

func TestParseProxyListAndBypass(t *testing.T) {
	if parseProxyList("  ", "") != nil || parseProxyList("http://", "") != nil {
		t.Fatal("lista vazia/inválida deveria resultar em nil")
	}
	sp := parseProxyList("proxy.local:3128", "<local>;*.empresa.local;10.*;intranet")
	if sp.proxyFor("https").String() != "http://proxy.local:3128" {
		t.Fatalf("proxy único: %v", sp.proxyFor("https"))
	}
	sp = parseProxyList("http=h1:80;https=h2:443", "")
	if sp.proxyFor("http").Host != "h1:80" || sp.proxyFor("https").Host != "h2:443" || sp.proxyFor("ftp") != nil {
		t.Fatal("proxy por esquema")
	}
	list := []string{"<local>", "*.empresa.local", "10.*", "intranet"}
	for host, want := range map[string]bool{
		"servidor": true, "127.0.0.1": true, "api.empresa.local": true, "10.1.2.3": true,
		"INTRANET": true, "monitor.example.com": false, "192.168.0.1": false,
	} {
		if bypass(host, list) != want {
			t.Errorf("bypass(%q) = %v", host, !want)
		}
	}
}

func TestManualProxyIsUsed(t *testing.T) {
	f, err := proxyFunc("http://proxy.local:8080")
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodGet, "https://monitor.example.com/api", nil)
	u, err := f(req)
	if err != nil || u.Host != "proxy.local:8080" {
		t.Fatalf("%v %v", u, err)
	}
}

// fakeServer implements the token endpoint (verifying the HMAC) and a few agent endpoints.
type fakeServer struct {
	t          *testing.T
	key        []byte
	serverTime time.Time
	skewFirst  bool
	revoked    atomic.Bool
	tokens     atomic.Int32
	reject401  atomic.Int32
	gotGzip    atomic.Bool
}

func (f *fakeServer) handler() http.Handler {
	mux := http.NewServeMux()
	writeErr := func(w http.ResponseWriter, status int, code string, st *time.Time) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		body := map[string]any{"detail": map[string]any{"code": code, "message": "erro " + code, "server_time": st}}
		_ = json.NewEncoder(w).Encode(body)
	}
	mux.HandleFunc("POST /api/agent/token", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.TokenRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			f.t.Error(err)
		}
		if f.revoked.Load() {
			writeErr(w, http.StatusUnauthorized, "agent_revoked", nil)
			return
		}
		if req.Signature != Sign(f.key, req.AgentID, req.Timestamp, req.Nonce) {
			writeErr(w, http.StatusUnauthorized, "bad_signature", nil)
			return
		}
		if d := req.Timestamp - f.serverTime.Unix(); f.skewFirst && (d > 300 || d < -300) {
			writeErr(w, http.StatusUnauthorized, "clock_skew", &f.serverTime)
			return
		}
		n := f.tokens.Add(1)
		_ = json.NewEncoder(w).Encode(protocol.TokenResponse{
			AccessToken: "tok" + strconv.Itoa(int(n)), ExpiresAt: f.serverTime.Add(15 * time.Minute), ServerTime: f.serverTime,
		})
	})
	mux.HandleFunc("POST /api/agent/heartbeat", func(w http.ResponseWriter, r *http.Request) {
		if f.reject401.Load() > 0 {
			f.reject401.Add(-1)
			writeErr(w, http.StatusUnauthorized, "token_expired", nil)
			return
		}
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer tok") {
			writeErr(w, http.StatusUnauthorized, "no_token", nil)
			return
		}
		_ = json.NewEncoder(w).Encode(protocol.HeartbeatResponse{ClusterRole: "master", ConfigVersion: 3})
	})
	mux.HandleFunc("POST /api/agent/readings", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Content-Encoding") != "gzip" {
			writeErr(w, http.StatusBadRequest, "not_gzip", nil)
			return
		}
		zr, err := gzip.NewReader(r.Body)
		if err != nil {
			writeErr(w, http.StatusBadRequest, "bad_gzip", nil)
			return
		}
		raw, _ := io.ReadAll(zr)
		var req protocol.ReadingsRequest
		if json.Unmarshal(raw, &req) != nil || req.V != protocol.Version {
			writeErr(w, http.StatusBadRequest, "bad_body", nil)
			return
		}
		f.gotGzip.Store(true)
		_ = json.NewEncoder(w).Encode(protocol.ReadingsResponse{})
	})
	mux.HandleFunc("GET /api/agent/config", func(w http.ResponseWriter, _ *http.Request) {
		writeErr(w, http.StatusUnprocessableEntity, "invalid", nil)
	})
	return mux
}

func newTestClient(t *testing.T, f *fakeServer) *Client {
	t.Helper()
	srv := httptest.NewServer(f.handler())
	t.Cleanup(srv.Close)
	c, err := New(Options{ServerURL: srv.URL}, "agent-1", f.key)
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestClientLearnsClockSkewAndAuthenticates(t *testing.T) {
	// Relógio do PC 2 h atrasado: o servidor responde clock_skew e o cliente corrige o deslocamento.
	f := &fakeServer{t: t, key: []byte("k"), serverTime: time.Now().Add(2 * time.Hour).UTC().Truncate(time.Second), skewFirst: true}
	c := newTestClient(t, f)
	resp, err := c.Heartbeat(context.Background(), protocol.HeartbeatRequest{})
	if err != nil {
		t.Fatal(err)
	}
	if resp.ClusterRole != "master" || resp.ConfigVersion != 3 {
		t.Fatalf("%+v", resp)
	}
	if off := c.ClockOffset(); off < 119*time.Minute || off > 121*time.Minute {
		t.Fatalf("deslocamento aprendido: %v", off)
	}
	// Token ainda válido: não pede outro.
	if _, err := c.Heartbeat(context.Background(), protocol.HeartbeatRequest{}); err != nil {
		t.Fatal(err)
	}
	if f.tokens.Load() != 1 {
		t.Fatalf("tokens emitidos: %d", f.tokens.Load())
	}
}

func TestClientRenewsTokenOn401AndUploadsGzip(t *testing.T) {
	f := &fakeServer{t: t, key: []byte("k"), serverTime: time.Now().UTC()}
	c := newTestClient(t, f)
	if _, err := c.Token(context.Background()); err != nil {
		t.Fatal(err)
	}
	f.reject401.Store(1)
	if _, err := c.Heartbeat(context.Background(), protocol.HeartbeatRequest{}); err != nil {
		t.Fatal(err)
	}
	if f.tokens.Load() != 2 {
		t.Fatalf("depois do 401 deveria renovar o token (emitidos: %d)", f.tokens.Load())
	}
	if _, err := c.UploadReadings(context.Background(), protocol.ReadingsRequest{}); err != nil || !f.gotGzip.Load() {
		t.Fatalf("envio gzip: %v", err)
	}
	_, err := c.Config(context.Background())
	var apiErr *Error
	if !errors.As(err, &apiErr) || apiErr.Status != http.StatusUnprocessableEntity || apiErr.Code != "invalid" || !apiErr.Permanent() {
		t.Fatalf("erro 422 esperado: %v", err)
	}
	if !strings.Contains(apiErr.Error(), "422") {
		t.Fatal(apiErr.Error())
	}
	if c.BaseURL().Host == "" || c.HTTPClient() == nil {
		t.Fatal("BaseURL/HTTPClient")
	}
}

func TestClientWrongKeyAndRevoked(t *testing.T) {
	f := &fakeServer{t: t, key: []byte("certa"), serverTime: time.Now().UTC()}
	srv := httptest.NewServer(f.handler())
	defer srv.Close()
	c, err := New(Options{ServerURL: srv.URL}, "agent-1", []byte("errada"))
	if err != nil {
		t.Fatal(err)
	}
	_, err = c.Heartbeat(context.Background(), protocol.HeartbeatRequest{})
	var apiErr *Error
	if !errors.As(err, &apiErr) || apiErr.Code != "bad_signature" || apiErr.Permanent() {
		t.Fatalf("assinatura errada: %v", err)
	}
	f.revoked.Store(true)
	if _, err := newTestClient(t, f).Heartbeat(context.Background(), protocol.HeartbeatRequest{}); !errors.Is(err, ErrRevoked) {
		t.Fatalf("revogado: %v", err)
	}
}

func TestNetworkErrorIsReported(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	base, _ := url.Parse(srv.URL)
	srv.Close()
	hc, _, err := NewHTTP(Options{ServerURL: base.String(), Timeout: 2 * time.Second})
	if err != nil {
		t.Fatal(err)
	}
	err = doJSON(context.Background(), hc, base, http.MethodGet, "/x", "", nil, nil, false)
	if err == nil || !strings.Contains(err.Error(), "falha de rede") {
		t.Fatalf("%v", err)
	}
}
