// Package release verifies and signs dm-agent / dm-watchdog releases (PROMPT 5.2).
//
// The public key is embedded in the binaries (public.key); the private key never goes to the server:
// releases are signed locally or in CI with `dm-tool sign`. The signature covers
// protocol.ReleaseMessage — component, version, OS, architecture and sha256 — so an old signed binary
// cannot be installed as a newer version, nor a binary built for another target.
package release

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"runtime"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

//go:embed public.key
var embeddedPublicKey string

// PublicKey returns the embedded release public key.
func PublicKey() (ed25519.PublicKey, error) {
	return ParsePublicKey(embeddedPublicKey)
}

// ParsePublicKey decodes a base64 ed25519 public key (32 bytes).
func ParsePublicKey(s string) (ed25519.PublicKey, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(s))
	if err != nil {
		return nil, fmt.Errorf("chave pública de releases inválida: %w", err)
	}
	if len(raw) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("chave pública de releases com %d bytes (esperado %d)", len(raw), ed25519.PublicKeySize)
	}
	return ed25519.PublicKey(raw), nil
}

// ParsePrivateKey decodes a base64 ed25519 private key (64 bytes, or the 32-byte seed).
func ParsePrivateKey(s string) (ed25519.PrivateKey, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(s))
	if err != nil {
		return nil, fmt.Errorf("chave privada inválida: %w", err)
	}
	switch len(raw) {
	case ed25519.PrivateKeySize:
		return ed25519.PrivateKey(raw), nil
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(raw), nil
	default:
		return nil, fmt.Errorf("chave privada com %d bytes (esperado 64 ou 32)", len(raw))
	}
}

// LoadPrivateKey reads a private key file written by `dm-tool keygen`.
func LoadPrivateKey(path string) (ed25519.PrivateKey, error) {
	raw, err := os.ReadFile(path) //nolint:gosec // G304: caminho informado pelo operador no dm-tool
	if err != nil {
		return nil, fmt.Errorf("ler chave privada: %w", err)
	}
	return ParsePrivateKey(string(raw))
}

// GenerateKey creates a new key pair, both base64.
func GenerateKey() (publicB64, privateB64 string, err error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", "", err
	}
	return base64.StdEncoding.EncodeToString(pub), base64.StdEncoding.EncodeToString(priv), nil
}

// SHA256 returns the hex digest of data.
func SHA256(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// Sign returns the base64 signature of a release binary and its sha256.
func Sign(priv ed25519.PrivateKey, component, version, goos, goarch string, data []byte) (signature, digest string) {
	digest = SHA256(data)
	sig := ed25519.Sign(priv, protocol.ReleaseMessage(component, version, goos, goarch, digest))
	return base64.StdEncoding.EncodeToString(sig), digest
}

// Errors of Verify (the watchdog reports them as the reason of a failed update).
var (
	ErrWrongTarget = errors.New("versão compilada para outro sistema/arquitetura")
	ErrSize        = errors.New("tamanho do arquivo baixado difere do publicado")
	ErrDigest      = errors.New("sha256 do arquivo baixado não confere")
	ErrSignature   = errors.New("assinatura ed25519 inválida")
)

// Verify checks a downloaded binary against the update params: target, size, sha256 and signature.
func Verify(pub ed25519.PublicKey, p protocol.UpdateParams, data []byte) error {
	if p.OS != runtime.GOOS || p.Arch != runtime.GOARCH {
		return fmt.Errorf("%w: %s/%s (este PC é %s/%s)", ErrWrongTarget, p.OS, p.Arch, runtime.GOOS, runtime.GOARCH)
	}
	if p.SizeBytes > 0 && int64(len(data)) != p.SizeBytes {
		return fmt.Errorf("%w: %d bytes (esperado %d)", ErrSize, len(data), p.SizeBytes)
	}
	if SHA256(data) != strings.ToLower(p.SHA256) {
		return ErrDigest
	}
	return VerifySignature(pub, p.Component, p.Version, p.OS, p.Arch, data, p.Signature)
}

// VerifySignature checks only the signature of a binary for the given component/version/target.
func VerifySignature(pub ed25519.PublicKey, component, version, goos, goarch string, data []byte, signature string) error {
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(signature))
	if err != nil || !ed25519.Verify(pub, protocol.ReleaseMessage(component, version, goos, goarch, SHA256(data)), sig) {
		return ErrSignature
	}
	return nil
}
