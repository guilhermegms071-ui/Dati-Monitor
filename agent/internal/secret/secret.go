// Package secret stores the agent's 32-byte enrollment secret in the data directory: protected with
// DPAPI (machine scope) on Windows, as a 0600 file elsewhere. The server keeps only a derived key.
package secret

import (
	"crypto/sha256"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// FileName of the protected secret.
const FileName = "credential"

// Size of the secret in bytes.
const Size = 32

// ErrMissing means there is no stored secret.
var ErrMissing = errors.New("credencial do coletor não encontrada (cadastre o coletor novamente)")

// Save protects and writes the secret.
func Save(dir string, secret []byte) error {
	if len(secret) != Size {
		return fmt.Errorf("segredo deve ter %d bytes", Size)
	}
	blob, err := protect(secret)
	if err != nil {
		return fmt.Errorf("proteger credencial: %w", err)
	}
	path := filepath.Join(dir, FileName)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, blob, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// Load reads and unprotects the secret.
func Load(dir string) ([]byte, error) {
	blob, err := os.ReadFile(filepath.Join(dir, FileName)) //nolint:gosec // G304: pasta de dados do agente
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrMissing
	}
	if err != nil {
		return nil, err
	}
	s, err := unprotect(blob)
	if err != nil {
		return nil, fmt.Errorf("ler credencial protegida: %w", err)
	}
	if len(s) != Size {
		return nil, errors.New("credencial com tamanho inválido")
	}
	return s, nil
}

// DeriveKey returns K = SHA-256("dm-agent-auth\n" + secret), the HMAC key shared with the server.
func DeriveKey(secret []byte) []byte {
	h := sha256.New()
	h.Write([]byte("dm-agent-auth\n"))
	h.Write(secret)
	return h.Sum(nil)
}
