package release

import (
	"crypto/ed25519"
	"encoding/base64"
	"errors"
	"runtime"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

func testKey(t *testing.T) ed25519.PrivateKey {
	t.Helper()
	seed := make([]byte, ed25519.SeedSize)
	for i := range seed {
		seed[i] = byte(i)
	}
	return ed25519.NewKeyFromSeed(seed)
}

// Vetor gerado pelo backend (cryptography + app.schemas.agent.release_message): Go e servidor assinam e
// conferem exatamente a mesma mensagem.
func TestSignatureMatchesTheServer(t *testing.T) {
	priv := testKey(t)
	pubB64 := base64.StdEncoding.EncodeToString(priv.Public().(ed25519.PublicKey))
	if pubB64 != "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=" {
		t.Fatalf("chave pública: %s", pubB64)
	}
	sig, digest := Sign(priv, "agent", "1.2.3", "windows", "amd64", []byte("dm-agent 1.2.3 windows amd64"))
	if digest != "e7fb9ce69d7397565d939873d452715243aac32844b61fb4f0eeb6eee5e6b22f" {
		t.Fatalf("sha256: %s", digest)
	}
	if sig != "76feWlWxFA8QVLX3JvRoS8iV6SzlWfJcsOL4lxud61xzLt4LFxl4HEr9SLTsXrxkiyfhy1e1tcKg+Kkw8bZHAw==" {
		t.Fatalf("assinatura diferente da do servidor: %s", sig)
	}
}

func params(priv ed25519.PrivateKey, version string, data []byte) protocol.UpdateParams {
	sig, digest := Sign(priv, "agent", version, runtime.GOOS, runtime.GOARCH, data)
	return protocol.UpdateParams{
		Component: "agent", Version: version, OS: runtime.GOOS, Arch: runtime.GOARCH,
		SHA256: digest, Signature: sig, SizeBytes: int64(len(data)),
	}
}

func TestVerify(t *testing.T) {
	priv := testKey(t)
	pub := priv.Public().(ed25519.PublicKey)
	data := []byte("binário 1.2.0")
	ok := params(priv, "1.2.0", data)
	if err := Verify(pub, ok, data); err != nil {
		t.Fatalf("assinatura válida recusada: %v", err)
	}
	cases := map[string]struct {
		p    protocol.UpdateParams
		data []byte
		want error
	}{
		"arquivo adulterado": {ok, []byte("binário 1.2.X"), ErrDigest},
		"tamanho":            {ok, []byte("binário 1.2.0!"), ErrSize},
		"outro alvo":         {func() protocol.UpdateParams { p := ok; p.Arch = "sparc"; return p }(), data, ErrWrongTarget},
		"versão trocada":     {func() protocol.UpdateParams { p := ok; p.Version = "9.9.9"; return p }(), data, ErrSignature},
		"componente trocado": {
			func() protocol.UpdateParams { p := ok; p.Component = "watchdog"; return p }(), data, ErrSignature,
		},
		"assinatura inválida": {func() protocol.UpdateParams { p := ok; p.Signature = "!!"; return p }(), data, ErrSignature},
	}
	for name, c := range cases {
		if err := Verify(pub, c.p, c.data); !errors.Is(err, c.want) {
			t.Errorf("%s: erro %v, esperado %v", name, err, c.want)
		}
	}
	_, other, _ := ed25519.GenerateKey(nil)
	if err := Verify(other.Public().(ed25519.PublicKey), ok, data); !errors.Is(err, ErrSignature) {
		t.Errorf("outra chave: %v", err)
	}
}

func TestKeysRoundTrip(t *testing.T) {
	pubB64, privB64, err := GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	pub, err := ParsePublicKey(pubB64)
	if err != nil {
		t.Fatal(err)
	}
	priv, err := ParsePrivateKey(privB64)
	if err != nil {
		t.Fatal(err)
	}
	seedOnly, err := ParsePrivateKey(base64.StdEncoding.EncodeToString(priv.Seed()))
	if err != nil || !seedOnly.Equal(priv) {
		t.Fatalf("semente: %v", err)
	}
	sig, _ := Sign(priv, "watchdog", "1.0.0", "linux", "arm", []byte("x"))
	if err := VerifySignature(pub, "watchdog", "1.0.0", "linux", "arm", []byte("x"), sig); err != nil {
		t.Fatal(err)
	}
	if _, err := ParsePublicKey("AAAA"); err == nil {
		t.Fatal("chave curta aceita")
	}
	if _, err := ParsePrivateKey("não é base64"); err == nil {
		t.Fatal("chave inválida aceita")
	}
	if _, err := PublicKey(); err != nil {
		t.Fatalf("chave pública embutida inválida: %v", err)
	}
}
