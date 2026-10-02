package webproxy

import (
	"context"
	"encoding/base64"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

type recorder struct {
	mu     sync.Mutex
	frames []any
}

func (r *recorder) send(_ context.Context, _ string, data any) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.frames = append(r.frames, data)
	return nil
}

func (r *recorder) body(t *testing.T) (protocol.WebResponseStart, string, *protocol.WebError) {
	t.Helper()
	var start protocol.WebResponseStart
	var b strings.Builder
	for _, f := range r.frames {
		switch v := f.(type) {
		case protocol.WebResponseStart:
			start = v
		case protocol.WebChunk:
			raw, err := base64.StdEncoding.DecodeString(v.DataB64)
			if err != nil {
				t.Fatal(err)
			}
			b.Write(raw)
		case protocol.WebError:
			return start, "", &v
		}
	}
	return start, b.String(), nil
}

func hostPort(t *testing.T, u string) (string, int) {
	t.Helper()
	host, port, err := net.SplitHostPort(strings.TrimPrefix(strings.TrimPrefix(u, "https://"), "http://"))
	if err != nil {
		t.Fatal(err)
	}
	p, _ := strconv.Atoi(port)
	return host, p
}

// withPort lets the test servers (random ports) pass the port allowlist.
func withPort(t *testing.T, port int) {
	t.Helper()
	old := protocol.WebPorts
	protocol.WebPorts = append(cloneInts(old), port)
	t.Cleanup(func() { protocol.WebPorts = old })
}

func cloneInts(v []int) []int { return append([]int(nil), v...) }

func open(t *testing.T, p *Proxy, id, scheme, url string, bps int) {
	t.Helper()
	ip, port := hostPort(t, url)
	withPort(t, port)
	if err := p.Open(protocol.WebProxyOpenParams{
		SessionID: id, IP: ip, Port: port, Scheme: scheme, ExpiresAt: time.Now().Add(time.Minute), MaxBytesPerSecond: bps,
	}); err != nil {
		t.Fatal(err)
	}
}

func TestServeForwardsRequestAndStreamsBody(t *testing.T) {
	big := strings.Repeat("x", 100<<10)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path == "/login" {
			w.Header().Add("Set-Cookie", "sess=1; Path=/")
			// Location montada à mão (sem http.Redirect): a impressora de teste ecoa o corpo e o cookie.
			w.Header().Set("Location", "/home?x="+string(body)+"&c="+r.Header.Get("Cookie"))
			w.WriteHeader(http.StatusFound)
			return
		}
		_, _ = io.WriteString(w, big)
	}))
	defer srv.Close()
	rec := &recorder{}
	p := New(slog.New(slog.DiscardHandler), rec.send)
	open(t, p, "s1", "http", srv.URL, 1<<20)

	body := base64.StdEncoding.EncodeToString([]byte("abc"))
	p.Serve(context.Background(), protocol.WebRequest{
		StreamID: "a", SessionID: "s1", Method: "POST", Path: "/login", BodyB64: &body,
		Headers: [][2]string{{"Cookie", "k=v"}, {"Connection", "close"}},
	})
	start, _, perr := rec.body(t)
	if perr != nil {
		t.Fatal(perr.Message)
	}
	if start.Status != http.StatusFound {
		t.Fatalf("status %d: o coletor não segue redirecionamentos (o navegador segue o reescrito)", start.Status)
	}
	var loc, cookie string
	for _, h := range start.Headers {
		switch h[0] {
		case "Location":
			loc = h[1]
		case "Set-Cookie":
			cookie = h[1]
		}
	}
	if loc != "/home?x=abc&c=k=v" || cookie != "sess=1; Path=/" {
		t.Fatalf("Location=%q Set-Cookie=%q", loc, cookie)
	}

	rec.frames = nil
	p.Serve(context.Background(), protocol.WebRequest{StreamID: "b", SessionID: "s1", Method: "GET", Path: "/big"})
	_, got, perr := rec.body(t)
	if perr != nil || got != big {
		t.Fatalf("corpo de %d bytes (erro %v)", len(got), perr)
	}
	last := rec.frames[len(rec.frames)-1].(protocol.WebChunk)
	if !last.End || len(rec.frames) < 4 {
		t.Fatalf("esperado em pedaços com fim marcado; %d quadros", len(rec.frames))
	}
}

func TestSelfSignedHTTPSAndBandwidthLimit(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = io.WriteString(w, strings.Repeat("y", 96<<10))
	}))
	defer srv.Close()
	rec := &recorder{}
	p := New(slog.New(slog.DiscardHandler), rec.send)
	open(t, p, "tls", "https", srv.URL, 32<<10) // 32 KiB/s: 96 KiB levam ~2 s
	started := time.Now()
	p.Serve(context.Background(), protocol.WebRequest{StreamID: "c", SessionID: "tls", Method: "GET", Path: "/"})
	_, got, perr := rec.body(t)
	if perr != nil {
		t.Fatal(perr.Message)
	}
	if len(got) != 96<<10 {
		t.Fatalf("corpo com %d bytes", len(got))
	}
	if el := time.Since(started); el < 1500*time.Millisecond {
		t.Fatalf("limite de banda não aplicado: %s", el)
	}
}

func TestRefusals(t *testing.T) {
	rec := &recorder{}
	p := New(slog.New(slog.DiscardHandler), rec.send)
	future := time.Now().Add(time.Minute)
	cases := []protocol.WebProxyOpenParams{
		{SessionID: "x", IP: "10.0.0.5", Port: 22, Scheme: "http", ExpiresAt: future},
		{SessionID: "x", IP: "nao-ip", Port: 80, Scheme: "http", ExpiresAt: future},
		{SessionID: "x", IP: "224.0.0.1", Port: 80, Scheme: "http", ExpiresAt: future},
		{SessionID: "x", IP: "10.0.0.5", Port: 80, Scheme: "ftp", ExpiresAt: future},
		{SessionID: "x", IP: "10.0.0.5", Port: 80, Scheme: "http", ExpiresAt: time.Now().Add(-time.Second)},
	}
	for _, c := range cases {
		if err := p.Open(c); err == nil {
			t.Errorf("abriu %+v", c)
		}
	}
	p.Serve(context.Background(), protocol.WebRequest{StreamID: "z", SessionID: "desconhecida", Method: "GET", Path: "/"})
	if _, _, perr := rec.body(t); perr == nil || !strings.Contains(perr.Message, "desconhecida") {
		t.Fatalf("pedido de sessão desconhecida deveria virar web_error: %+v", perr)
	}
	// Caminho absoluto para outro host nunca sai da sessão.
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer srv.Close()
	open(t, p, "ok", "http", srv.URL, 1<<20)
	rec.frames = nil
	p.Serve(context.Background(), protocol.WebRequest{StreamID: "w", SessionID: "ok", Method: "GET", Path: "//10.9.9.9/admin"})
	if _, _, perr := rec.body(t); perr == nil {
		t.Fatal("caminho //host deveria ser recusado")
	}
	// Sessão expirada deixa de responder.
	p.Now = func() time.Time { return time.Now().Add(2 * time.Minute) }
	rec.frames = nil
	p.Serve(context.Background(), protocol.WebRequest{StreamID: "e", SessionID: "ok", Method: "GET", Path: "/"})
	if _, _, perr := rec.body(t); perr == nil || !strings.Contains(perr.Message, "expirada") {
		t.Fatalf("sessão expirada deveria ser recusada: %+v", perr)
	}
}
