// Package webproxy is the collector side of the printer web page tunnel (PROMPT 4.9). The server opens a
// session with the web_proxy_open command (one IP, one port, until an expiry time); the gateway then
// forwards browser requests as web_request frames and this package answers them with web_response and
// web_chunk frames, limited to the session's bandwidth. Nothing outside the open sessions is reachable.
package webproxy

import (
	"context"
	"crypto/tls"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// Limits of the tunnel.
const (
	ChunkSize      = 32 << 10
	RequestTimeout = 60 * time.Second
	MaxSessions    = 20
)

// Sender writes a frame on the WebSocket (web_response, web_chunk, web_error).
type Sender func(ctx context.Context, typ string, data any) error

type session struct {
	ip, scheme string
	port       int
	expires    time.Time
	limiter    *rate.Limiter
}

// Proxy holds the open sessions and serves their requests.
type Proxy struct {
	Log    *slog.Logger
	Send   Sender
	Client *http.Client
	Now    func() time.Time

	mu       sync.Mutex
	sessions map[string]*session
}

// New prepares the proxy. The HTTP client accepts self-signed printer certificates (the user always
// talks HTTPS with the server) and never follows redirects (the browser follows the rewritten ones).
func New(log *slog.Logger, send Sender) *Proxy {
	tr := &http.Transport{
		Proxy:                 nil, // a impressora está na rede local: nunca pelo proxy da internet
		DialContext:           (&net.Dialer{Timeout: 10 * time.Second}).DialContext,
		TLSClientConfig:       printerTLS(),
		ResponseHeaderTimeout: 30 * time.Second,
		MaxIdleConnsPerHost:   4,
		IdleConnTimeout:       60 * time.Second,
		DisableCompression:    true,
	}
	return &Proxy{
		Log: log, Send: send, Now: time.Now, sessions: map[string]*session{},
		Client: &http.Client{
			Transport:     tr,
			Timeout:       RequestTimeout,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
	}
}

// Open validates and registers a session (command web_proxy_open).
func (p *Proxy) Open(params protocol.WebProxyOpenParams) error {
	ip := net.ParseIP(params.IP)
	if ip == nil || ip.To4() == nil {
		return fmt.Errorf("IP inválido: %q", params.IP)
	}
	if ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() {
		return fmt.Errorf("IP não permitido: %s", params.IP)
	}
	if !slices.Contains(protocol.WebPorts, params.Port) {
		return fmt.Errorf("porta não permitida: %d", params.Port)
	}
	if params.Scheme != "http" && params.Scheme != "https" {
		return fmt.Errorf("protocolo não permitido: %q", params.Scheme)
	}
	if params.SessionID == "" || !params.ExpiresAt.After(p.Now()) {
		return errors.New("sessão sem identificador ou já expirada")
	}
	bps := max(params.MaxBytesPerSecond, 16<<10)
	p.mu.Lock()
	defer p.mu.Unlock()
	p.purge()
	if len(p.sessions) >= MaxSessions {
		return errors.New("sessões de acesso web demais abertas neste coletor")
	}
	p.sessions[params.SessionID] = &session{
		ip: params.IP, port: params.Port, scheme: params.Scheme, expires: params.ExpiresAt,
		limiter: rate.NewLimiter(rate.Limit(bps), max(bps, ChunkSize)),
	}
	p.Log.Info("acesso web aberto", "sessao", params.SessionID, "destino", net.JoinHostPort(params.IP, strconv.Itoa(params.Port)),
		"expira", params.ExpiresAt)
	return nil
}

func (p *Proxy) purge() {
	now := p.Now()
	for id, s := range p.sessions {
		if !s.expires.After(now) {
			delete(p.sessions, id)
		}
	}
}

func (p *Proxy) get(id string) (*session, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	s, ok := p.sessions[id]
	if !ok {
		return nil, errors.New("sessão de acesso web desconhecida neste coletor")
	}
	if !s.expires.After(p.Now()) {
		delete(p.sessions, id)
		return nil, errors.New("sessão de acesso web expirada")
	}
	return s, nil
}

var hopByHop = map[string]bool{
	"connection": true, "keep-alive": true, "proxy-authenticate": true, "proxy-authorization": true,
	"te": true, "trailer": true, "transfer-encoding": true, "upgrade": true, "host": true, "content-length": true,
}

// Serve answers one forwarded request. Every failure becomes a web_error frame (never silent).
func (p *Proxy) Serve(ctx context.Context, req protocol.WebRequest) {
	if err := p.serve(ctx, req); err != nil {
		p.Log.Warn("acesso web: pedido não atendido", "sessao", req.SessionID, "caminho", req.Path, "erro", err)
		if sendErr := p.Send(ctx, protocol.WSWebError, protocol.WebError{
			V: protocol.Version, StreamID: req.StreamID, Message: err.Error(),
		}); sendErr != nil {
			p.Log.Error("acesso web: não foi possível avisar o servidor da falha", "erro", sendErr)
		}
	}
}

func (p *Proxy) serve(ctx context.Context, req protocol.WebRequest) error {
	s, err := p.get(req.SessionID)
	if err != nil {
		return err
	}
	if !strings.HasPrefix(req.Path, "/") || strings.HasPrefix(req.Path, "//") {
		return fmt.Errorf("caminho inválido: %q", req.Path)
	}
	var body io.Reader
	if req.BodyB64 != nil && *req.BodyB64 != "" {
		raw, err := base64.StdEncoding.DecodeString(*req.BodyB64)
		if err != nil {
			return fmt.Errorf("corpo em base64 inválido: %w", err)
		}
		body = strings.NewReader(string(raw))
	}
	// O destino vem SEMPRE da sessão (nunca do pedido): só o IP e a porta abertos pelo servidor.
	target := s.scheme + "://" + net.JoinHostPort(s.ip, strconv.Itoa(s.port)) + req.Path
	hreq, err := http.NewRequestWithContext(ctx, req.Method, target, body)
	if err != nil {
		return fmt.Errorf("pedido inválido: %w", err)
	}
	for _, h := range req.Headers {
		if !hopByHop[strings.ToLower(h[0])] {
			hreq.Header.Add(h[0], h[1])
		}
	}
	resp, err := p.Client.Do(hreq)
	if err != nil {
		return fmt.Errorf("impressora não respondeu: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	start := protocol.WebResponseStart{V: protocol.Version, StreamID: req.StreamID, Status: resp.StatusCode}
	for k, vs := range resp.Header {
		if hopByHop[strings.ToLower(k)] {
			continue
		}
		for _, v := range vs {
			start.Headers = append(start.Headers, [2]string{k, v})
		}
	}
	if err := p.Send(ctx, protocol.WSWebResponse, start); err != nil {
		return fmt.Errorf("enviar cabeçalhos: %w", err)
	}
	buf := make([]byte, ChunkSize)
	for {
		n, rerr := io.ReadFull(resp.Body, buf)
		end := errors.Is(rerr, io.EOF) || errors.Is(rerr, io.ErrUnexpectedEOF)
		if rerr != nil && !end {
			return fmt.Errorf("ler a resposta da impressora: %w", rerr)
		}
		if n > 0 {
			if err := s.limiter.WaitN(ctx, n); err != nil {
				return fmt.Errorf("limite de banda: %w", err)
			}
		}
		chunk := protocol.WebChunk{V: protocol.Version, StreamID: req.StreamID, End: end}
		if n > 0 {
			chunk.DataB64 = base64.StdEncoding.EncodeToString(buf[:n])
		}
		if err := p.Send(ctx, protocol.WSWebChunk, chunk); err != nil {
			return fmt.Errorf("enviar resposta: %w", err)
		}
		if end {
			return nil
		}
	}
}

// printerTLS is the TLS of the printer's web page: self-signed certificates (4.9) and old firmwares that
// only speak TLS 1.0 with RSA key exchange (Go 1.22+ no longer offers those suites by default; the
// printer then closes the handshake and the user saw "EOF"). Only for this LAN tunnel, never for the API.
func printerTLS() *tls.Config {
	suites := make([]uint16, 0, 32)
	for _, s := range tls.CipherSuites() {
		suites = append(suites, s.ID)
	}
	for _, s := range tls.InsecureCipherSuites() {
		suites = append(suites, s.ID)
	}
	return &tls.Config{
		InsecureSkipVerify: true, //nolint:gosec // impressoras usam certificado autoassinado (4.9)
		MinVersion:         tls.VersionTLS10,
		CipherSuites:       suites,
	}
}
