package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/commands"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// SetServerMaxAge is how long a signed server change stays valid (the command may wait in the queue).
const SetServerMaxAge = 7 * 24 * time.Hour

// SwitchDelay lets the result reach the current server before the collector moves to the new one.
var SwitchDelay = 3 * time.Second

type setServerParams struct {
	ServerURL string `json:"server_url"`
	WSURL     string `json:"ws_url"`
	IssuedAt  int64  `json:"issued_at"`
	Signature string `json:"signature"`
}

// cmdSetServer moves the collector to another server (e.g. from the local network to the hosting) without
// reinstalling. It only switches when: the server signed the change with this collector's key; the
// address follows the same rules as the enrollment (HTTPS, or http:// of a private IP when installed with
// --insecure-lan); and the collector authenticates on the NEW server with its own credential. Otherwise
// it stays on the current server and reports why.
func (a *Agent) cmdSetServer(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	p, err := decode[setServerParams](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	p.ServerURL = strings.TrimRight(strings.TrimSpace(p.ServerURL), "/")
	if p.ServerURL == "" || p.Signature == "" {
		return commands.Result{}, errors.New("comando sem endereço ou sem assinatura")
	}
	if !a.Client.VerifySetServer(p.ServerURL, p.WSURL, p.IssuedAt, p.Signature) {
		return commands.Result{}, errors.New("assinatura do novo endereço não confere com a chave deste coletor: ignorado")
	}
	issued := time.Unix(p.IssuedAt, 0)
	if age := a.Client.ServerNow().Sub(issued); age > SetServerMaxAge || age < -5*time.Minute {
		return commands.Result{}, fmt.Errorf("pedido de troca de servidor vencido (emitido em %s)", issued.UTC().Format(time.RFC3339))
	}
	a.mu.Lock()
	opts := api.Options{
		ServerURL: p.ServerURL, InsecureDev: a.Local.InsecureDev, InsecureLAN: a.Local.InsecureLAN, ProxyURL: a.Local.ProxyURL,
	}
	old := a.Local.ServerURL
	a.mu.Unlock()
	progress("conferindo o novo servidor com a credencial do coletor")
	probe, err := a.Client.WithServer(opts)
	if err != nil {
		return commands.Result{}, fmt.Errorf("endereço recusado: %w", err)
	}
	pctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	if _, err := probe.Token(pctx); err != nil {
		return commands.Result{}, fmt.Errorf("o novo servidor não autenticou este coletor (continua em %s): %w", old, err)
	}
	if err := a.persistServer(p.ServerURL, p.WSURL); err != nil {
		return commands.Result{}, err
	}
	a.Log.Info("novo endereço do servidor validado; troca em instantes", "de", old, "para", p.ServerURL)
	go func() {
		time.Sleep(SwitchDelay)
		a.switchServer(opts)
	}()
	return commands.Result{
		Data:   map[string]any{"from": old, "to": p.ServerURL, "ws_url": a.wsURL()},
		Output: fmt.Sprintf("Servidor validado. O coletor passa a usar %s.", p.ServerURL),
	}, nil
}

// persistServer saves the new address (the watchdog picks it up from the same file).
func (a *Agent) persistServer(serverURL, wsURL string) error {
	a.mu.Lock()
	defer a.mu.Unlock()
	next := *a.Local
	next.ServerURL, next.WSURL = serverURL, wsURL
	if next.Server != nil {
		cfg := *next.Server
		cfg.WSURL = "" // o canal do servidor antigo não vale mais; o novo servidor manda o dele
		next.Server = &cfg
	}
	if err := config.Save(a.Dir, &next); err != nil {
		return fmt.Errorf("salvar o novo endereço: %w", err)
	}
	*a.Local = next
	return nil
}

func (a *Agent) switchServer(opts api.Options) {
	if err := a.Client.SwitchServer(opts); err != nil {
		a.Log.Error("trocar de servidor", "erro", err)
		return
	}
	a.WS.Reconnect()
	a.Uploader.Kick()
	a.Log.Info("coletor usando o novo servidor", "servidor", opts.ServerURL)
}
