// Package ws is the agent's persistent WebSocket channel (PROMPT 4.3): authenticated with the session
// token, reconnects forever with exponential backoff and jitter (1 s → 60 s), pings every 20 s and
// reconnects after 2 missed pongs. Heartbeats and command updates go out through it; commands and
// cancellations come in.
package ws

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/coder/websocket"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/backoff"
	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// Tunables (PROMPT 4.3).
const (
	PingInterval   = 20 * time.Second
	PingTimeout    = 10 * time.Second
	MaxMissedPongs = 2
	WriteTimeout   = 10 * time.Second
	AckTimeout     = 15 * time.Second
	MaxMessage     = 16 << 20
	// A session that lasted this long resets the reconnection backoff.
	StableAfter = 10 * time.Second
	// Close codes sent by the gateway (4000–4999 range).
	CloseReplaced     = 4000
	CloseUnauthorized = 4401
	CloseRevoked      = 4403
)

// ErrNotConnected is returned by Send when there is no live connection.
var ErrNotConnected = errors.New("canal WebSocket desconectado")

// ErrRejected means the server answered the command update with an error (it will not accept it).
var ErrRejected = errors.New("servidor recusou a atualização do comando")

// Handler receives server messages. Methods are called from the read loop: they must not block.
type Handler interface {
	OnConnected()
	OnCommand(cmd protocol.CommandMessage)
	OnCancel(id string)
}

// Channel is the WebSocket connection manager.
type Channel struct {
	Client  *api.Client
	URL     func() string // ws(s)://…/ws/agent (from the server configuration)
	Handler Handler
	Log     *slog.Logger
	OnError func(msg string) // last error (shown in /health)
	// Capabilities advertised in the hello message (command types this build executes).
	Capabilities []string
	// PingEvery / PingWait default to PingInterval / PingTimeout (fields so tests can shorten them).
	PingEvery time.Duration
	PingWait  time.Duration

	mu        sync.Mutex
	conn      *websocket.Conn
	sessDone  chan struct{} // fechado quando a conexão atual termina
	writeMu   sync.Mutex
	downSince time.Time
	connected atomic.Bool
	rttMicros atomic.Int64
	revoked   atomic.Bool
	lastUp    atomic.Int64 // UnixNano da última conexão estabelecida
	reconnect chan struct{}
	hbAck     chan protocol.HeartbeatResponse
	updAcks   sync.Map // id → chan protocol.CommandUpdateResponse
}

// New prepares the channel (Run starts it).
func New(client *api.Client, wsURL func() string, h Handler, log *slog.Logger) *Channel {
	now := time.Now()
	return &Channel{
		Client: client, URL: wsURL, Handler: h, Log: log, downSince: now, PingEvery: PingInterval, PingWait: PingTimeout,
		reconnect: make(chan struct{}, 1), hbAck: make(chan protocol.HeartbeatResponse, 1),
	}
}

// Connected reports whether the WebSocket is up.
func (c *Channel) Connected() bool { return c.connected.Load() }

// Revoked reports whether the server closed the channel because the agent was revoked.
func (c *Channel) Revoked() bool { return c.revoked.Load() }

// DownFor is how long the channel has been down (0 when connected).
func (c *Channel) DownFor() time.Duration {
	if c.connected.Load() {
		return 0
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return time.Since(c.downSince)
}

// RTT is the last ping round trip (nil before the first pong).
func (c *Channel) RTT() *float64 {
	us := c.rttMicros.Load()
	if us == 0 {
		return nil
	}
	ms := float64(us) / 1000
	return &ms
}

// Reconnect closes the current connection; Run reconnects immediately (command "reconnect").
func (c *Channel) Reconnect() {
	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn != nil {
		_ = conn.Close(websocket.StatusNormalClosure, "reconnect requested")
	}
	select {
	case c.reconnect <- struct{}{}:
	default:
	}
}

// Run keeps the channel up until ctx ends.
func (c *Channel) Run(ctx context.Context) {
	bo := backoff.New(time.Second, time.Minute)
	for ctx.Err() == nil {
		start := time.Now()
		err := c.session(ctx)
		if ctx.Err() != nil {
			return
		}
		var ce websocket.CloseError
		serverRestart := errors.As(err, &ce) &&
			(ce.Code == websocket.StatusServiceRestart || ce.Code == websocket.StatusGoingAway || ce.Code == websocket.StatusTryAgainLater)
		if c.connectedAt().After(start) && (time.Since(start) > StableAfter || serverRestart) {
			// A sessão chegou a funcionar (ou o servidor só reiniciou): volta a tentar rápido.
			bo.Reset()
		}
		wait := bo.Next()
		switch {
		case errors.As(err, &ce) && ce.Code == CloseRevoked:
			c.revoked.Store(true)
			c.report("coletor revogado no portal")
			c.Log.Error("canal WebSocket recusado: coletor revogado no portal; cadastre-o novamente")
			wait = 5 * time.Minute
		case errors.As(err, &ce) && ce.Code == CloseUnauthorized:
			c.Client.ResetToken()
			c.report("token recusado pelo gateway: " + ce.Reason)
			c.Log.Warn("gateway recusou o token; renovando", "motivo", ce.Reason)
		case errors.As(err, &ce) && ce.Code == websocket.StatusNormalClosure:
			c.Log.Info("canal WebSocket fechado; reconectando", "motivo", ce.Reason)
		case err != nil:
			c.report(err.Error())
			c.Log.Warn("canal WebSocket caiu", "erro", err, "nova_tentativa_s", wait.Seconds())
		}
		select {
		case <-ctx.Done():
			return
		case <-c.reconnect:
			bo.Reset()
		case <-time.After(wait):
		}
	}
}

func (c *Channel) connectedAt() time.Time { return time.Unix(0, c.lastUp.Load()) }

func (c *Channel) report(msg string) {
	if c.OnError != nil {
		c.OnError(msg)
	}
}

// Endpoint derives the WebSocket URL from the server URL when the server did not send one.
func Endpoint(serverURL, configured string) string {
	if configured != "" {
		return configured
	}
	u, err := url.Parse(strings.TrimRight(serverURL, "/"))
	if err != nil {
		return ""
	}
	if u.Scheme == "https" {
		u.Scheme = "wss"
	} else {
		u.Scheme = "ws"
	}
	u.Path = "/ws/agent"
	return u.String()
}

func (c *Channel) session(ctx context.Context) error {
	target := c.URL()
	if target == "" {
		return errors.New("endereço do WebSocket desconhecido")
	}
	tok, err := c.Client.Token(ctx)
	if err != nil {
		return fmt.Errorf("autenticar: %w", err)
	}
	dctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	conn, resp, err := websocket.Dial(dctx, target, &websocket.DialOptions{
		HTTPClient: c.Client.WSHTTPClient(),
		HTTPHeader: http.Header{"Authorization": {"Bearer " + tok}, "User-Agent": {"dm-agent/" + buildinfo.Version}},
	})
	cancel()
	if resp != nil && resp.Body != nil {
		_ = resp.Body.Close()
	}
	if err != nil {
		return fmt.Errorf("conectar em %s: %w", target, err)
	}
	conn.SetReadLimit(MaxMessage)
	sctx, stop := context.WithCancel(ctx)
	defer stop()
	done := make(chan struct{})
	c.mu.Lock()
	c.conn, c.sessDone = conn, done
	c.mu.Unlock()
	defer func() {
		c.connected.Store(false)
		c.mu.Lock()
		c.conn = nil
		c.downSince = time.Now()
		c.mu.Unlock()
		close(done) // quem espera confirmação nesta conexão desiste na hora (e usa o HTTPS)
		_ = conn.CloseNow()
	}()
	if err := c.write(sctx, conn, protocol.WSHello, protocol.Hello{
		V: protocol.Version, Version: buildinfo.Version, Capabilities: c.Capabilities,
	}); err != nil {
		return err
	}
	c.connected.Store(true)
	c.lastUp.Store(time.Now().UnixNano())
	c.revoked.Store(false)
	c.report("")
	c.Log.Info("canal WebSocket conectado", "servidor", target)
	go c.pinger(sctx, conn, stop)
	c.Handler.OnConnected()
	return c.readLoop(sctx, conn)
}

func (c *Channel) pinger(ctx context.Context, conn *websocket.Conn, stop context.CancelFunc) {
	t := time.NewTicker(c.PingEvery)
	defer t.Stop()
	missed := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		pctx, cancel := context.WithTimeout(ctx, c.PingWait)
		start := time.Now()
		err := conn.Ping(pctx)
		cancel()
		if err != nil {
			missed++
			c.Log.Warn("ping do WebSocket sem resposta", "falhas", missed, "erro", err)
			if missed >= MaxMissedPongs {
				_ = conn.Close(websocket.StatusGoingAway, "pong timeout")
				stop()
				return
			}
			continue
		}
		missed = 0
		c.rttMicros.Store(max(time.Since(start).Microseconds(), 1))
	}
}

func (c *Channel) readLoop(ctx context.Context, conn *websocket.Conn) error {
	for {
		typ, raw, err := conn.Read(ctx)
		if err != nil {
			return err
		}
		if typ != websocket.MessageText {
			continue
		}
		var msg protocol.WSMessage
		if err := json.Unmarshal(raw, &msg); err != nil {
			c.Log.Error("mensagem inválida do servidor", "erro", err)
			continue
		}
		c.dispatch(msg)
	}
}

func (c *Channel) dispatch(msg protocol.WSMessage) {
	switch msg.Type {
	case protocol.WSWelcome:
		var w protocol.Welcome
		if err := json.Unmarshal(msg.Data, &w); err == nil {
			c.Log.Debug("boas-vindas do gateway", "horario_servidor", w.ServerTime)
		}
	case protocol.WSCommand:
		var cmd protocol.CommandMessage
		if err := json.Unmarshal(msg.Data, &cmd); err != nil || cmd.ID == "" {
			c.Log.Error("comando mal formado recebido", "erro", err)
			return
		}
		c.Handler.OnCommand(cmd)
	case protocol.WSCancel:
		var d struct {
			ID string `json:"id"`
		}
		if json.Unmarshal(msg.Data, &d) == nil && d.ID != "" {
			c.Handler.OnCancel(d.ID)
		}
	case protocol.WSHeartbeatAck:
		var r protocol.HeartbeatResponse
		if err := json.Unmarshal(msg.Data, &r); err != nil {
			c.Log.Error("confirmação de heartbeat inválida", "erro", err)
			return
		}
		select {
		case c.hbAck <- r:
		default:
		}
	case protocol.WSCommandUpdateAck:
		var r protocol.CommandUpdateResponse
		if json.Unmarshal(msg.Data, &r) == nil {
			if ch, ok := c.updAcks.Load(r.ID); ok {
				select {
				case ch.(chan protocol.CommandUpdateResponse) <- r:
				default:
				}
			}
		}
	case protocol.WSError:
		var e protocol.WSErrorData
		_ = json.Unmarshal(msg.Data, &e)
		c.Log.Error("servidor recusou mensagem do coletor", "codigo", e.Code, "mensagem", e.Message, "id", e.ID)
		if e.ID != "" {
			if ch, ok := c.updAcks.Load(e.ID); ok {
				select {
				case ch.(chan protocol.CommandUpdateResponse) <- protocol.CommandUpdateResponse{ID: e.ID, State: "error:" + e.Code}:
				default:
				}
			}
		}
	default:
		c.Log.Warn("tipo de mensagem desconhecido do servidor", "tipo", msg.Type)
	}
}

func (c *Channel) write(ctx context.Context, conn *websocket.Conn, typ string, data any) error {
	raw, err := json.Marshal(data)
	if err != nil {
		return err
	}
	msg, err := json.Marshal(protocol.WSMessage{V: protocol.Version, Type: typ, Data: raw})
	if err != nil {
		return err
	}
	wctx, cancel := context.WithTimeout(ctx, WriteTimeout)
	defer cancel()
	c.writeMu.Lock()
	defer c.writeMu.Unlock()
	return conn.Write(wctx, websocket.MessageText, msg)
}

func (c *Channel) current() (*websocket.Conn, <-chan struct{}) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.connected.Load() {
		return nil, nil
	}
	return c.conn, c.sessDone
}

// Heartbeat sends a heartbeat and waits for the server's answer.
func (c *Channel) Heartbeat(ctx context.Context, req protocol.HeartbeatRequest) (*protocol.HeartbeatResponse, error) {
	conn, done := c.current()
	if conn == nil {
		return nil, ErrNotConnected
	}
	select { // descarta uma confirmação antiga que tenha chegado atrasada
	case <-c.hbAck:
	default:
	}
	req.V = protocol.Version
	if err := c.write(ctx, conn, protocol.WSHeartbeat, req); err != nil {
		return nil, err
	}
	select {
	case r := <-c.hbAck:
		return &r, nil
	case <-done:
		return nil, ErrNotConnected
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-time.After(AckTimeout):
		return nil, errors.New("servidor não confirmou o heartbeat pelo WebSocket")
	}
}

// CommandUpdate sends a command update and waits for its acknowledgement.
func (c *Channel) CommandUpdate(ctx context.Context, upd protocol.CommandUpdate) error {
	conn, done := c.current()
	if conn == nil {
		return ErrNotConnected
	}
	ch := make(chan protocol.CommandUpdateResponse, 1)
	c.updAcks.Store(upd.ID, ch)
	defer c.updAcks.Delete(upd.ID)
	upd.V = protocol.Version
	if err := c.write(ctx, conn, protocol.WSCommandUpdate, upd); err != nil {
		return err
	}
	select {
	case r := <-ch:
		if strings.HasPrefix(r.State, "error:") {
			return fmt.Errorf("%w: %s", ErrRejected, strings.TrimPrefix(r.State, "error:"))
		}
		return nil
	case <-done:
		return ErrNotConnected
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(AckTimeout):
		return errors.New("servidor não confirmou a atualização do comando")
	}
}
