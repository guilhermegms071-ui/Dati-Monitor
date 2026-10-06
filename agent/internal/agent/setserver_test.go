package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
)

// Mesmo vetor do backend (tests/test_commands.py): Go e Python assinam igual.
func TestSetServerSignatureMatchesTheServer(t *testing.T) {
	key := make([]byte, 32)
	for i := range key {
		key[i] = byte(i)
	}
	got := api.SetServerSignature(key, "agent-1", "https://monitor.exemplo.com.br", "wss://monitor.exemplo.com.br/ws/agent", 1700000000)
	if got != setServerVector {
		t.Fatalf("assinatura %s, servidor calcula %s", got, setServerVector)
	}
}

// "Mudar endereço do servidor": só troca com assinatura válida, pedido no prazo e o NOVO servidor
// autenticando o coletor com a credencial dele; senão continua no atual e informa o motivo.
func TestSetServerSwitchesOnlyToAValidatedServer(t *testing.T) {
	prev := SwitchDelay
	SwitchDelay = 50 * time.Millisecond
	t.Cleanup(func() { SwitchDelay = prev })
	current := &fakeServer{}
	dir, _ := setup(t, current)
	key := current.key

	next := &fakeServer{key: key} // a hospedagem migrada: mesma base de coletores
	nextSrv := httptest.NewServer(next.handler(t))
	t.Cleanup(nextSrv.Close)
	impostor := &fakeServer{key: secret.DeriveKey(bytes.Repeat([]byte{7}, secret.Size)), rejectQuietly: true}
	impSrv := httptest.NewServer(impostor.handler(t)) // recusa o token: não conhece a chave do coletor
	t.Cleanup(impSrv.Close)

	a, err := New(dir, quiet())
	if err != nil {
		t.Fatal(err)
	}
	a.ContingencyAfter, a.PollInterval, a.ReconnectWait = 0, 50*time.Millisecond, 300*time.Millisecond
	now := time.Now().UTC()
	cmd := func(id, url string, issued time.Time, sig string) protocol.CommandMessage {
		if sig == "" {
			sig = api.SetServerSignature(key, "agent-1", url, "", issued.Unix())
		}
		params, _ := json.Marshal(map[string]any{"server_url": url, "ws_url": "", "issued_at": issued.Unix(), "signature": sig})
		return protocol.CommandMessage{ID: id, Type: "set_server", Params: params, CreatedAt: now, ExpiresAt: now.Add(time.Hour)}
	}
	current.mu.Lock()
	current.pending = []protocol.CommandMessage{
		cmd("bad-sig", nextSrv.URL, now, strings.Repeat("0", 64)),
		cmd("impostor", impSrv.URL, now, ""),
		cmd("old", nextSrv.URL, now.Add(-8*24*time.Hour), ""),
		cmd("http-public", "http://8.8.8.8:8000", now, ""),
	}
	current.mu.Unlock()

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- a.Run(ctx) }()
	defer func() { cancel(); <-done }()

	want := map[string]string{
		"bad-sig":     "assinatura do novo endereço não confere",
		"impostor":    "não autenticou este coletor",
		"old":         "vencido",
		"http-public": "HTTPS",
	}
	for id, msg := range want {
		u := waitUpdate(t, current, id)
		if u.State != protocol.StateFailed || !strings.Contains(u.Error, msg) {
			t.Fatalf("%s: %+v (esperado falha com %q)", id, u, msg)
		}
	}
	if l, _ := config.Load(dir); l.ServerURL == nextSrv.URL || l.ServerURL == impSrv.URL {
		t.Fatalf("trocou de servidor sem validação: %s", l.ServerURL)
	}

	current.mu.Lock()
	current.pending = []protocol.CommandMessage{cmd("ok", nextSrv.URL, now, "")}
	current.mu.Unlock()
	if u := waitUpdate(t, current, "ok"); u.State != protocol.StateSucceeded || u.Result["to"] != nextSrv.URL {
		t.Fatalf("troca válida: %+v", u)
	}
	if l, err := config.Load(dir); err != nil || l.ServerURL != nextSrv.URL || (l.Server != nil && l.Server.WSURL != "") {
		t.Fatalf("configuração salva: %+v %v", l, err)
	}
	// Depois da troca, o coletor passa a falar com o novo servidor (a busca de comandos chega lá).
	deadline := time.Now().Add(15 * time.Second)
	for {
		next.mu.Lock()
		n := next.polls
		next.mu.Unlock()
		if n > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("o coletor não passou a falar com o novo servidor")
		}
		time.Sleep(50 * time.Millisecond)
	}
	if a.Client.BaseURL().String() != nextSrv.URL {
		t.Fatalf("cliente ainda aponta para %s", a.Client.BaseURL())
	}
}

func waitUpdate(t *testing.T, f *fakeServer, id string) protocol.CommandUpdate {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for {
		if u, ok := f.finalUpdate(id); ok {
			return u
		}
		if time.Now().After(deadline) {
			t.Fatalf("comando %s não terminou", id)
		}
		time.Sleep(30 * time.Millisecond)
	}
}

// setServerVector = app.core.security.set_server_signature(bytes(range(32)), "agent-1",
// "https://monitor.exemplo.com.br", "wss://monitor.exemplo.com.br/ws/agent", 1700000000).
const setServerVector = "47b35e12c44e8df0fb944350a185bcb06204ab1bf635ee609c068cfb2d0bacd1"
