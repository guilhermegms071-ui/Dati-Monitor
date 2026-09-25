package secret

import (
	"bytes"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/api"
)

func TestSaveLoadRoundTrip(t *testing.T) {
	dir := t.TempDir()
	if _, err := Load(dir); !errors.Is(err, ErrMissing) {
		t.Fatalf("sem arquivo: %v", err)
	}
	if err := Save(dir, []byte("curto")); err == nil {
		t.Fatal("segredo com tamanho errado deveria falhar")
	}
	s := bytes.Repeat([]byte{7}, Size)
	if err := Save(dir, s); err != nil {
		t.Fatal(err)
	}
	blob, err := os.ReadFile(filepath.Join(dir, FileName))
	if err != nil {
		t.Fatal(err)
	}
	if runtime.GOOS == "windows" && bytes.Contains(blob, s) {
		t.Fatal("o segredo não pode ficar em claro no disco (DPAPI)")
	}
	got, err := Load(dir)
	if err != nil || !bytes.Equal(got, s) {
		t.Fatalf("ida e volta: %x %v", got, err)
	}
	if err := os.WriteFile(filepath.Join(dir, FileName), []byte("lixo"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(dir); err == nil {
		t.Fatal("arquivo corrompido deveria falhar")
	}
}

// The reference values come from the backend (app.core.security.derive_agent_key / agent_signature),
// so agent and server provably derive the same key and signature.
func TestDeriveKeyMatchesServer(t *testing.T) {
	s := make([]byte, Size)
	for i := range s {
		s[i] = byte(i)
	}
	k := DeriveKey(s)
	if hex.EncodeToString(k) != "03ada24b9edaaad871ddda58e47734bf5008de16e64c927dfb499f09cbcdb29c" {
		t.Fatalf("K = %x", k)
	}
	if sig := api.Sign(k, "agent-1", 1790000000, "abc"); sig != "678cdb5f0667a153d05b360a00ef0e07a29324f7fb29e122f04075631a867514" {
		t.Fatalf("assinatura = %s", sig)
	}
}
