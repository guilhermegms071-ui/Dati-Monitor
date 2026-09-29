// Package api is the agent's HTTPS client for /api/agent/*: enrollment, HMAC-signed session
// tokens, heartbeat, configuration and gzip'ed reading batches. Plain HTTP is only accepted for
// localhost or with --insecure-dev (PROMPT 12).
package api

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// Options configure the HTTP transport.
type Options struct {
	ServerURL   string
	InsecureDev bool
	ProxyURL    string
	Timeout     time.Duration
}

// Error is a non-2xx answer from the server.
type Error struct {
	Status     int
	Code       string
	Message    string
	serverTime *time.Time
}

func (e *Error) Error() string {
	if e.Message != "" {
		return fmt.Sprintf("servidor respondeu %d (%s): %s", e.Status, e.Code, e.Message)
	}
	return fmt.Sprintf("servidor respondeu %d", e.Status)
}

// Permanent reports client errors that retrying will not fix (except auth, timeouts and throttling).
func (e *Error) Permanent() bool {
	switch e.Status {
	case http.StatusUnauthorized, http.StatusRequestTimeout, http.StatusTooManyRequests, http.StatusConflict:
		return false
	}
	return e.Status >= 400 && e.Status < 500
}

// ErrRevoked means the portal revoked this collector.
var ErrRevoked = errors.New("coletor revogado no portal")

// NewHTTP builds the HTTP client and validates the server URL.
func NewHTTP(opts Options) (*http.Client, *url.URL, error) {
	u, err := url.Parse(strings.TrimRight(opts.ServerURL, "/"))
	if err != nil || u.Host == "" {
		return nil, nil, fmt.Errorf("endereço do servidor inválido: %q", opts.ServerURL)
	}
	switch u.Scheme {
	case "https":
	case "http":
		if !isLoopback(u.Hostname()) && !opts.InsecureDev {
			return nil, nil, fmt.Errorf("o servidor precisa usar HTTPS (http:// só é aceito para localhost ou com --insecure-dev)")
		}
	default:
		return nil, nil, fmt.Errorf("esquema não suportado: %q", u.Scheme)
	}
	proxy, err := proxyFunc(opts.ProxyURL)
	if err != nil {
		return nil, nil, err
	}
	timeout := opts.Timeout
	if timeout <= 0 {
		timeout = 60 * time.Second
	}
	tr := &http.Transport{
		Proxy:               proxy,
		DialContext:         (&net.Dialer{Timeout: 15 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
		TLSHandshakeTimeout: 15 * time.Second,
		IdleConnTimeout:     90 * time.Second,
		MaxIdleConnsPerHost: 4,
		ForceAttemptHTTP2:   true,
	}
	return &http.Client{Transport: tr, Timeout: timeout}, u, nil
}

func isLoopback(host string) bool {
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func userAgent() string {
	return fmt.Sprintf("%s-agent/%s", product.Slug, buildinfo.Version)
}

// Enroll exchanges an enrollment code for the agent id and secret.
func Enroll(ctx context.Context, opts Options, req protocol.EnrollRequest) (*protocol.EnrollResponse, error) {
	hc, base, err := NewHTTP(opts)
	if err != nil {
		return nil, err
	}
	req.V = protocol.Version
	var out protocol.EnrollResponse
	if err := doJSON(ctx, hc, base, http.MethodPost, "/api/agent/enroll", "", req, &out, false); err != nil {
		return nil, err
	}
	return &out, nil
}

// Client is an authenticated agent session.
type Client struct {
	hc      *http.Client
	base    *url.URL
	agentID string
	key     []byte

	mu          sync.Mutex
	token       string
	tokenExp    time.Time
	clockOffset time.Duration
	now         func() time.Time
}

// New creates the authenticated client. key = secret.DeriveKey(secret).
func New(opts Options, agentID string, key []byte) (*Client, error) {
	hc, base, err := NewHTTP(opts)
	if err != nil {
		return nil, err
	}
	return &Client{hc: hc, base: base, agentID: agentID, key: key, now: time.Now}, nil
}

// ServerNow returns the current time on the server's clock (local clock corrected by the offset
// learned from the token endpoint).
func (c *Client) ServerNow() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now().Add(c.clockOffset)
}

// ClockOffset is server time minus local time.
func (c *Client) ClockOffset() time.Duration {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.clockOffset
}

// Sign computes the token signature (exported for tests and the watchdog).
func Sign(key []byte, agentID string, ts int64, nonce string) string {
	m := hmac.New(sha256.New, key)
	_, _ = fmt.Fprintf(m, "%s\n%d\n%s", agentID, ts, nonce)
	return hex.EncodeToString(m.Sum(nil))
}

func (c *Client) authenticate(ctx context.Context) error {
	for attempt := 0; attempt < 2; attempt++ {
		nonceBytes := make([]byte, 16)
		if _, err := rand.Read(nonceBytes); err != nil {
			return err
		}
		nonce := hex.EncodeToString(nonceBytes)
		ts := c.ServerNow().Unix()
		req := protocol.TokenRequest{
			V: protocol.Version, AgentID: c.agentID, Timestamp: ts, Nonce: nonce, Signature: Sign(c.key, c.agentID, ts, nonce),
		}
		var out protocol.TokenResponse
		err := doJSON(ctx, c.hc, c.base, http.MethodPost, "/api/agent/token", "", req, &out, false)
		var apiErr *Error
		if errors.As(err, &apiErr) && apiErr.Code == "clock_skew" && apiErr.serverTime != nil && attempt == 0 {
			// Relógio do PC muito diferente do servidor: aprende a diferença e tenta de novo.
			c.mu.Lock()
			c.clockOffset = apiErr.serverTime.Sub(c.now())
			c.mu.Unlock()
			continue
		}
		if errors.As(err, &apiErr) && apiErr.Code == "agent_revoked" {
			return ErrRevoked
		}
		if err != nil {
			return err
		}
		c.mu.Lock()
		c.token, c.tokenExp = out.AccessToken, out.ExpiresAt
		if !out.ServerTime.IsZero() {
			c.clockOffset = out.ServerTime.Sub(c.now())
		}
		c.mu.Unlock()
		return nil
	}
	return errors.New("não foi possível autenticar o coletor (relógio do sistema)")
}

func (c *Client) bearer(ctx context.Context) (string, error) {
	c.mu.Lock()
	valid := c.token != "" && c.now().Add(c.clockOffset).Before(c.tokenExp.Add(-60*time.Second))
	tok := c.token
	c.mu.Unlock()
	if valid {
		return tok, nil
	}
	if err := c.authenticate(ctx); err != nil {
		return "", err
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.token, nil
}

// Token returns a valid session token (used by the WebSocket channel).
func (c *Client) Token(ctx context.Context) (string, error) { return c.bearer(ctx) }

// BaseURL returns the server base URL.
func (c *Client) BaseURL() *url.URL { u := *c.base; return &u }

// HTTPClient exposes the configured client (proxy, TLS) for other channels.
func (c *Client) HTTPClient() *http.Client { return c.hc }

func (c *Client) call(ctx context.Context, method, path string, in, out any, gz bool) error {
	for attempt := 0; attempt < 2; attempt++ {
		tok, err := c.bearer(ctx)
		if err != nil {
			return err
		}
		err = doJSON(ctx, c.hc, c.base, method, path, tok, in, out, gz)
		var apiErr *Error
		if errors.As(err, &apiErr) && apiErr.Status == http.StatusUnauthorized && attempt == 0 {
			if apiErr.Code == "agent_revoked" {
				return ErrRevoked
			}
			c.mu.Lock()
			c.token = ""
			c.mu.Unlock()
			continue
		}
		return err
	}
	return errors.New("falha de autenticação repetida")
}

// Heartbeat sends a heartbeat over HTTPS.
func (c *Client) Heartbeat(ctx context.Context, req protocol.HeartbeatRequest) (*protocol.HeartbeatResponse, error) {
	req.V = protocol.Version
	var out protocol.HeartbeatResponse
	if err := c.call(ctx, http.MethodPost, "/api/agent/heartbeat", req, &out, false); err != nil {
		return nil, err
	}
	return &out, nil
}

// Config fetches the site configuration.
func (c *Client) Config(ctx context.Context) (*protocol.AgentConfig, error) {
	var out protocol.AgentConfig
	if err := c.call(ctx, http.MethodGet, "/api/agent/config", nil, &out, false); err != nil {
		return nil, err
	}
	return &out, nil
}

// UploadReadings sends a batch (gzip).
func (c *Client) UploadReadings(ctx context.Context, req protocol.ReadingsRequest) (*protocol.ReadingsResponse, error) {
	req.V = protocol.Version
	var out protocol.ReadingsResponse
	if err := c.call(ctx, http.MethodPost, "/api/agent/readings", req, &out, true); err != nil {
		return nil, err
	}
	return &out, nil
}

// PendingCommands fetches commands over HTTPS (contingency channel when the WebSocket is down).
func (c *Client) PendingCommands(ctx context.Context) ([]protocol.CommandMessage, error) {
	var out protocol.PendingCommandsResponse
	if err := c.call(ctx, http.MethodGet, "/api/agent/commands/pending", nil, &out, false); err != nil {
		return nil, err
	}
	return out.Commands, nil
}

// CommandUpdate reports a command state over HTTPS.
func (c *Client) CommandUpdate(ctx context.Context, upd protocol.CommandUpdate) (*protocol.CommandUpdateResponse, error) {
	upd.V = protocol.Version
	var out protocol.CommandUpdateResponse
	path := "/api/agent/commands/" + url.PathEscape(upd.ID) + "/update"
	if err := c.call(ctx, http.MethodPost, path, upd, &out, false); err != nil {
		return nil, err
	}
	return &out, nil
}

// Upload sends a file produced by a command ("logs" → .zip, "mib-walk" → .snmprec.gz).
func (c *Client) Upload(ctx context.Context, kind, commandID, contentType string, data []byte) (*protocol.UploadResponse, error) {
	for attempt := 0; attempt < 2; attempt++ {
		tok, err := c.bearer(ctx)
		if err != nil {
			return nil, err
		}
		u := *c.base
		u.Path = strings.TrimRight(c.base.Path, "/") + "/api/agent/uploads/" + kind
		u.RawQuery = url.Values{"command_id": {commandID}}.Encode()
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", contentType)
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("User-Agent", userAgent())
		var out protocol.UploadResponse
		err = finish(c.hc, req, "/api/agent/uploads/"+kind, &out)
		var apiErr *Error
		if errors.As(err, &apiErr) && apiErr.Status == http.StatusUnauthorized && attempt == 0 {
			c.ResetToken()
			continue
		}
		if err != nil {
			return nil, err
		}
		return &out, nil
	}
	return nil, errors.New("falha de autenticação repetida")
}

// ResetToken forgets the session token (the next call authenticates again).
func (c *Client) ResetToken() {
	c.mu.Lock()
	c.token = ""
	c.mu.Unlock()
}

// WSHTTPClient is an HTTP client for the WebSocket handshake: same proxy and TLS settings, but
// HTTP/1.1 only (the Upgrade handshake does not exist in HTTP/2).
func (c *Client) WSHTTPClient() *http.Client {
	tr, ok := c.hc.Transport.(*http.Transport)
	if !ok {
		return &http.Client{Transport: c.hc.Transport}
	}
	t := tr.Clone()
	t.ForceAttemptHTTP2 = false
	t.TLSNextProto = map[string]func(string, *tls.Conn) http.RoundTripper{}
	if t.TLSClientConfig != nil {
		t.TLSClientConfig.NextProtos = []string{"http/1.1"}
	}
	return &http.Client{Transport: t, Timeout: 30 * time.Second}
}

// SuggestRanges sends the agent's private /24 networks for approval in the portal.
func (c *Client) SuggestRanges(ctx context.Context, ranges []string) error {
	req := protocol.SuggestRangesRequest{V: protocol.Version, Ranges: ranges}
	return c.call(ctx, http.MethodPost, "/api/agent/ranges/suggest", req, nil, false)
}

type errorBody struct {
	Detail struct {
		Code       string     `json:"code"`
		Message    string     `json:"message"`
		ServerTime *time.Time `json:"server_time"`
	} `json:"detail"`
}

func doJSON(ctx context.Context, hc *http.Client, base *url.URL, method, path, token string, in, out any, gz bool) error {
	var body io.Reader
	var encoding string
	if in != nil {
		raw, err := json.Marshal(in)
		if err != nil {
			return err
		}
		if gz {
			var buf bytes.Buffer
			zw := gzip.NewWriter(&buf)
			if _, err := zw.Write(raw); err != nil {
				return err
			}
			if err := zw.Close(); err != nil {
				return err
			}
			body, encoding = &buf, "gzip"
		} else {
			body = bytes.NewReader(raw)
		}
	}
	u := *base
	u.Path = strings.TrimRight(base.Path, "/") + path
	req, err := http.NewRequestWithContext(ctx, method, u.String(), body)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", userAgent())
	req.Header.Set("Accept", "application/json")
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if encoding != "" {
		req.Header.Set("Content-Encoding", encoding)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	return finish(hc, req, path, out)
}

// finish sends the request and decodes a JSON answer (or the error body).
func finish(hc *http.Client, req *http.Request, path string, out any) error {
	resp, err := hc.Do(req)
	if err != nil {
		return fmt.Errorf("falha de rede ao chamar %s: %w", path, err)
	}
	defer func() { _ = resp.Body.Close() }()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return fmt.Errorf("ler resposta de %s: %w", path, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		apiErr := &Error{Status: resp.StatusCode}
		var eb errorBody
		if json.Unmarshal(raw, &eb) == nil {
			apiErr.Code, apiErr.Message, apiErr.serverTime = eb.Detail.Code, eb.Detail.Message, eb.Detail.ServerTime
		}
		return apiErr
	}
	if out == nil || len(raw) == 0 {
		return nil
	}
	if err := json.Unmarshal(raw, out); err != nil {
		return fmt.Errorf("resposta inválida de %s: %w", path, err)
	}
	return nil
}

// WatchdogHeartbeat is dm-watchdog's own channel (POST /api/watchdog/heartbeat, PROMPT 5.1).
func (c *Client) WatchdogHeartbeat(ctx context.Context, req protocol.WatchdogHeartbeatRequest) (*protocol.WatchdogHeartbeatResponse, error) {
	req.V = protocol.Version
	var out protocol.WatchdogHeartbeatResponse
	if err := c.call(ctx, http.MethodPost, "/api/watchdog/heartbeat", req, &out, false); err != nil {
		return nil, err
	}
	return &out, nil
}

// Download fetches a binary served by the API (release of an `update`) with the session token.
func (c *Client) Download(ctx context.Context, path string, limit int64) ([]byte, error) {
	for attempt := 0; attempt < 2; attempt++ {
		tok, err := c.bearer(ctx)
		if err != nil {
			return nil, err
		}
		u := *c.base
		u.Path = strings.TrimRight(c.base.Path, "/") + path
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		req.Header.Set("User-Agent", userAgent())
		resp, err := c.hc.Do(req)
		if err != nil {
			return nil, fmt.Errorf("falha de rede ao baixar %s: %w", path, err)
		}
		data, readErr := io.ReadAll(io.LimitReader(resp.Body, limit+1))
		_ = resp.Body.Close()
		if resp.StatusCode == http.StatusUnauthorized && attempt == 0 {
			c.ResetToken()
			continue
		}
		if resp.StatusCode != http.StatusOK {
			return nil, &Error{Status: resp.StatusCode, Message: "download recusado pelo servidor"}
		}
		if readErr != nil {
			return nil, fmt.Errorf("baixar %s: %w", path, readErr)
		}
		if int64(len(data)) > limit {
			return nil, fmt.Errorf("arquivo de %s maior que o esperado (%d bytes)", path, limit)
		}
		return data, nil
	}
	return nil, errors.New("falha de autenticação repetida")
}
