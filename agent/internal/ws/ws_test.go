package ws

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// fakeGateway authenticates tokens issued by its own /api/agent/token and speaks the /ws/agent protocol.
type fakeGateway struct {
	t        *testing.T
	key      []byte
	tokens   atomic.Int32
	accepted atomic.Int32
	// closeWith, when set, closes new connections right after the handshake with this code.
	closeWith atomic.Int32
	// stall, when set, makes the NEXT established connection stop reading (so pongs are never sent).
	stall     atomic.Bool
	mu        sync.Mutex
	conns     []*websocket.Conn
	received  chan protocol.WSMessage
	rawByType sync.Map // tipo → bytes exatos da última mensagem desse tipo
}

func newGateway(t *testing.T) (*fakeGateway, *httptest.Server) {
	g := &fakeGateway{t: t, key: []byte("chave"), received: make(chan protocol.WSMessage, 64)}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/agent/token", func(w http.ResponseWriter, r *http.Request) {
		var req protocol.TokenRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		if req.Signature != api.Sign(g.key, req.AgentID, req.Timestamp, req.Nonce) {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		n := g.tokens.Add(1)
		now := time.Now().UTC()
		_ = json.NewEncoder(w).Encode(protocol.TokenResponse{
			AccessToken: "tok-" + strconv.Itoa(int(n)), ExpiresAt: now.Add(15 * time.Minute), ServerTime: now,
		})
	})
	mux.HandleFunc("/ws/agent", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer tok-") {
			http.Error(w, "sem token", http.StatusUnauthorized)
			return
		}
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		g.accepted.Add(1)
		if code := g.closeWith.Load(); code != 0 {
			_ = c.Close(websocket.StatusCode(code), "motivo-teste")
			return
		}
		g.mu.Lock()
		g.conns = append(g.conns, c)
		g.mu.Unlock()
		ctx := r.Context()
		if g.stall.CompareAndSwap(true, false) {
			<-ctx.Done() // não lê nada: pings do agente ficam sem pong
			return
		}
		for {
			_, raw, err := c.Read(ctx)
			if err != nil {
				return
			}
			var msg protocol.WSMessage
			if err := json.Unmarshal(raw, &msg); err != nil {
				t.Errorf("mensagem inválida do agente: %s", raw)
				return
			}
			g.rawByType.Store(msg.Type, string(raw))
			g.received <- msg
			switch msg.Type {
			case protocol.WSHeartbeat:
				g.send(c, protocol.WSHeartbeatAck, protocol.HeartbeatResponse{ClusterRole: "master", ConfigVersion: 7})
			case protocol.WSCommandUpdate:
				var u protocol.CommandUpdate
				_ = json.Unmarshal(msg.Data, &u)
				if u.ID == "desconhecido" {
					g.send(c, protocol.WSError, protocol.WSErrorData{Code: "not_found", Message: "Comando não encontrado", ID: u.ID})
				} else {
					g.send(c, protocol.WSCommandUpdateAck, protocol.CommandUpdateResponse{ID: u.ID, State: u.State})
				}
			}
		}
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return g, srv
}

func (g *fakeGateway) send(c *websocket.Conn, typ string, data any) {
	raw, _ := json.Marshal(data)
	msg, _ := json.Marshal(protocol.WSMessage{V: 1, Type: typ, Data: raw})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = c.Write(ctx, websocket.MessageText, msg)
}

func (g *fakeGateway) last() *websocket.Conn {
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(g.conns) == 0 {
		return nil
	}
	return g.conns[len(g.conns)-1]
}

type recorder struct {
	connected atomic.Int32
	commands  chan protocol.CommandMessage
	cancels   chan string
	web       chan protocol.WebRequest
}

func (r *recorder) OnConnected()                          { r.connected.Add(1) }
func (r *recorder) OnCommand(cmd protocol.CommandMessage) { r.commands <- cmd }
func (r *recorder) OnCancel(id string)                    { r.cancels <- id }
func (r *recorder) OnWebRequest(req protocol.WebRequest)  { r.web <- req }

func start(t *testing.T, g *fakeGateway, srv *httptest.Server) (*Channel, *recorder, context.CancelFunc) {
	t.Helper()
	client, err := api.New(api.Options{ServerURL: srv.URL}, "agent-1", g.key)
	if err != nil {
		t.Fatal(err)
	}
	rec := &recorder{
		commands: make(chan protocol.CommandMessage, 8), cancels: make(chan string, 8), web: make(chan protocol.WebRequest, 8),
	}
	url := Endpoint(srv.URL, "")
	ch := New(client, func() string { return url }, rec, slog.New(slog.NewTextHandler(io.Discard, nil)))
	ch.Capabilities = []string{"reconnect", "diagnostics"}
	var lastErr atomic.Value
	ch.OnError = func(m string) { lastErr.Store(m) }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { ch.Run(ctx); close(done) }()
	t.Cleanup(func() { cancel(); <-done })
	return ch, rec, cancel
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

func next(t *testing.T, g *fakeGateway, typ string) protocol.WSMessage {
	t.Helper()
	timeout := time.After(10 * time.Second)
	for {
		select {
		case m := <-g.received:
			if m.Type == typ {
				return m
			}
		case <-timeout:
			t.Fatalf("mensagem %s não chegou", typ)
		}
	}
}

func TestConnectHelloHeartbeatAndCommands(t *testing.T) {
	g, srv := newGateway(t)
	ch, rec, _ := start(t, g, srv)
	waitFor(t, "conexão", ch.Connected)
	hello := next(t, g, protocol.WSHello)
	var h protocol.Hello
	if err := json.Unmarshal(hello.Data, &h); err != nil || len(h.Capabilities) != 2 {
		t.Fatalf("hello: %s %v", hello.Data, err)
	}
	waitFor(t, "OnConnected", func() bool { return rec.connected.Load() == 1 })
	if ch.DownFor() != 0 || ch.Revoked() {
		t.Fatal("estado do canal")
	}

	resp, err := ch.Heartbeat(context.Background(), protocol.HeartbeatRequest{Hostname: "PC"})
	if err != nil || resp.ClusterRole != "master" || resp.ConfigVersion != 7 {
		t.Fatalf("heartbeat: %+v %v", resp, err)
	}
	var hb protocol.HeartbeatRequest
	_ = json.Unmarshal(next(t, g, protocol.WSHeartbeat).Data, &hb)
	if hb.Hostname != "PC" || hb.V != protocol.Version {
		t.Fatalf("heartbeat recebido: %+v", hb)
	}

	if err := ch.CommandUpdate(context.Background(), protocol.CommandUpdate{ID: "c1", State: protocol.StateSucceeded}); err != nil {
		t.Fatal(err)
	}
	err = ch.CommandUpdate(context.Background(), protocol.CommandUpdate{ID: "desconhecido", State: protocol.StateFailed})
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("erro do servidor deveria ser definitivo: %v", err)
	}

	conn := g.last()
	g.send(conn, protocol.WSCommand, protocol.CommandMessage{ID: "c2", Type: "diagnostics", Params: json.RawMessage(`{}`)})
	g.send(conn, protocol.WSCancel, map[string]string{"id": "c2"})
	g.send(conn, "tipo-novo", map[string]string{})
	select {
	case cmd := <-rec.commands:
		if cmd.ID != "c2" || cmd.Type != "diagnostics" {
			t.Fatalf("%+v", cmd)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("comando não chegou ao handler")
	}
	select {
	case id := <-rec.cancels:
		if id != "c2" {
			t.Fatal(id)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("cancelamento não chegou ao handler")
	}
}

func TestReconnectAndServerCloses(t *testing.T) {
	g, srv := newGateway(t)
	ch, rec, _ := start(t, g, srv)
	waitFor(t, "conexão", ch.Connected)
	ch.Reconnect()
	waitFor(t, "reconexão", func() bool { return g.accepted.Load() == 2 && ch.Connected() })
	if rec.connected.Load() != 2 {
		t.Fatalf("OnConnected: %d", rec.connected.Load())
	}

	// Gateway reiniciando (1012): mesmo com a sessão curta, volta em ~1 s, sem esperar o backoff crescer.
	before := g.accepted.Load()
	t0 := time.Now()
	_ = g.last().Close(websocket.StatusServiceRestart, "restart")
	waitFor(t, "volta após reinício do gateway", func() bool { return g.accepted.Load() > before && ch.Connected() })
	if d := time.Since(t0); d > 3*time.Second {
		t.Fatalf("reconexão após reinício do servidor demorou %s", d)
	}

	// Gateway fecha com 4401 (token recusado): o canal pede token novo e tenta de novo.
	g.closeWith.Store(CloseUnauthorized)
	tokens := g.tokens.Load()
	_ = g.last().Close(websocket.StatusCode(CloseUnauthorized), "token_invalid")
	waitFor(t, "renovação do token", func() bool { return g.tokens.Load() > tokens })

	// Coletor revogado: o canal marca e espera bem mais antes de tentar de novo.
	g.closeWith.Store(CloseRevoked)
	waitFor(t, "revogação", ch.Revoked)
	if ch.Connected() || ch.DownFor() <= 0 {
		t.Fatal("canal deveria estar fora")
	}
	if _, err := ch.Heartbeat(context.Background(), protocol.HeartbeatRequest{}); !errors.Is(err, ErrNotConnected) {
		t.Fatalf("heartbeat sem conexão: %v", err)
	}
	if err := ch.CommandUpdate(context.Background(), protocol.CommandUpdate{ID: "x"}); !errors.Is(err, ErrNotConnected) {
		t.Fatalf("atualização sem conexão: %v", err)
	}
}

func TestPingMeasuresRTTAndDetectsDeadServer(t *testing.T) {
	if PingInterval != 20*time.Second || MaxMissedPongs != 2 {
		t.Fatal("valores da seção 4.3 mudaram")
	}
	g, srv := newGateway(t)
	client, err := api.New(api.Options{ServerURL: srv.URL}, "agent-1", g.key)
	if err != nil {
		t.Fatal(err)
	}
	rec := &recorder{
		commands: make(chan protocol.CommandMessage, 8), cancels: make(chan string, 8), web: make(chan protocol.WebRequest, 8),
	}
	url := Endpoint(srv.URL, "")
	ch := New(client, func() string { return url }, rec, slog.New(slog.NewTextHandler(io.Discard, nil)))
	ch.PingEvery, ch.PingWait = 50*time.Millisecond, 200*time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { ch.Run(ctx); close(done) }()
	defer func() { cancel(); <-done }()
	waitFor(t, "medição de RTT", func() bool { return ch.RTT() != nil && *ch.RTT() > 0 })

	// Servidor "congelado": a próxima conexão nunca devolve o pong. Depois de 2 pings perdidos o
	// canal fecha e reconecta sozinho (numa conexão normal).
	g.stall.Store(true)
	before := g.accepted.Load()
	ch.Reconnect()
	waitFor(t, "reconexão após pongs perdidos", func() bool { return g.accepted.Load() >= before+2 && ch.Connected() })
}

func TestEndpoint(t *testing.T) {
	cases := map[[2]string]string{
		{"https://monitor.example.com", ""}:            "wss://monitor.example.com/ws/agent",
		{"http://127.0.0.1:8000/", ""}:                 "ws://127.0.0.1:8000/ws/agent",
		{"http://127.0.0.1:8000", "ws://x:1/ws/agent"}: "ws://x:1/ws/agent",
	}
	for in, want := range cases {
		if got := Endpoint(in[0], in[1]); got != want {
			t.Errorf("%v -> %s, esperado %s", in, got, want)
		}
	}
}

// TestWebTunnelFrames: web_request from the gateway reaches the handler; the answer frames go out with
// "v" and "type" first (the gateway recognizes tunnel frames by that prefix).
func TestWebTunnelFrames(t *testing.T) {
	g, srv := newGateway(t)
	ch, rec, _ := start(t, g, srv)
	waitFor(t, "conexão", ch.Connected)
	next(t, g, protocol.WSHello)
	g.send(g.last(), protocol.WSWebRequest, protocol.WebRequest{StreamID: "s1", SessionID: "x", Method: "GET", Path: "/"})
	select {
	case req := <-rec.web:
		if req.StreamID != "s1" || req.Path != "/" {
			t.Fatalf("pedido: %+v", req)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("web_request não chegou ao handler")
	}
	if err := ch.Send(context.Background(), protocol.WSWebChunk, protocol.WebChunk{V: 1, StreamID: "s1", End: true}); err != nil {
		t.Fatal(err)
	}
	next(t, g, protocol.WSWebChunk)
	raw, _ := g.rawByType.Load(protocol.WSWebChunk)
	if !strings.HasPrefix(raw.(string), `{"v":1,"type":"web_chunk"`) {
		t.Fatalf("prefixo do quadro: %s", raw)
	}
}
