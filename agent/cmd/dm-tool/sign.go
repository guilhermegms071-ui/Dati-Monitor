package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/release"
)

// defaultKeyPath is where `keygen` writes the private key: outside any repository, in the user's home.
func defaultKeyPath() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return "release-signing.key"
	}
	return filepath.Join(home, ".dati-monitor", "release-signing.key")
}

func cmdKeygen(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("keygen", flag.ContinueOnError)
	fs.SetOutput(stderr)
	out := fs.String("out", defaultKeyPath(), "arquivo da chave PRIVADA (guarde em local seguro; nunca no servidor)")
	pubOut := fs.String("public-out", "", "arquivo da chave pública (ex.: agent/internal/release/public.key)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if _, err := os.Stat(*out); err == nil {
		return fail(stderr, "%s já existe: não sobrescrevo uma chave de assinatura (apague-a conscientemente antes)", *out)
	}
	pub, priv, err := release.GenerateKey()
	if err != nil {
		return fail(stderr, "gerar chave: %v", err)
	}
	if err := os.MkdirAll(filepath.Dir(*out), 0o700); err != nil {
		return fail(stderr, "%v", err)
	}
	if err := os.WriteFile(*out, []byte(priv+"\n"), 0o600); err != nil {
		return fail(stderr, "gravar chave privada: %v", err)
	}
	if *pubOut != "" {
		if err := os.WriteFile(*pubOut, []byte(pub+"\n"), 0o644); err != nil { //nolint:gosec // G306: chave pública
			return fail(stderr, "gravar chave pública: %v", err)
		}
	}
	_, _ = fmt.Fprintf(stdout, "Chave privada: %s\nChave pública (embuta no watchdog e em RELEASE_PUBLIC_KEY): %s\n", *out, pub)
	return 0
}

type signFlags struct {
	file, component, version, goos, goarch, key string
}

func (s *signFlags) register(fs *flag.FlagSet) {
	fs.StringVar(&s.file, "file", "", "binário a assinar (dm-agent ou dm-watchdog)")
	fs.StringVar(&s.component, "component", "", "agent ou watchdog (padrão: pelo nome do arquivo)")
	fs.StringVar(&s.version, "version", "", "versão publicada (ex.: 1.2.0)")
	fs.StringVar(&s.goos, "os", runtime.GOOS, "sistema do binário: windows ou linux")
	fs.StringVar(&s.goarch, "arch", runtime.GOARCH, "arquitetura: amd64, 386, arm64 ou arm")
}

func (s *signFlags) validate() error {
	if s.file == "" || s.version == "" {
		return errors.New("informe --file e --version")
	}
	if s.component == "" {
		base := strings.ToLower(filepath.Base(s.file))
		switch {
		case strings.HasPrefix(base, "dm-agent"):
			s.component = "agent"
		case strings.HasPrefix(base, "dm-watchdog"):
			s.component = "watchdog"
		default:
			return errors.New("informe --component (agent ou watchdog)")
		}
	}
	if s.component != "agent" && s.component != "watchdog" {
		return fmt.Errorf("componente %q inválido (agent ou watchdog)", s.component)
	}
	return nil
}

// selfVersion runs `<file> version` when the binary is for this machine and checks it reports the
// version being published (otherwise the update would never pass the health check).
func selfVersion(s signFlags) error {
	if s.goos != runtime.GOOS || s.goarch != runtime.GOARCH {
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, s.file, "version").Output() //nolint:gosec // G204: binário informado pelo operador
	if err != nil {
		return fmt.Errorf("executar %s version: %w", s.file, err)
	}
	if !versionLineMatches(string(out), s.component, s.version) {
		return fmt.Errorf("o binário se declara %q, não dm-%s %s: confira o arquivo e compile com -Version %s",
			strings.TrimSpace(string(out)), s.component, s.version, s.version)
	}
	return nil
}

// versionLineMatches checks the `version` output ("<produto> dm-agent 1.2.0 (commit …)"): the binary must be
// the right program (dm-agent or dm-watchdog, not dm-tool) AND report exactly the version being published.
func versionLineMatches(out, component, version string) bool {
	return strings.Contains(" "+out, " dm-"+component+" "+version+" (")
}

func cmdSign(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("sign", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var s signFlags
	s.register(fs)
	fs.StringVar(&s.key, "key", defaultKeyPath(), "chave privada (gerada por dm-tool keygen)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if err := s.validate(); err != nil {
		return fail(stderr, "%v", err)
	}
	if err := selfVersion(s); err != nil {
		return fail(stderr, "%v", err)
	}
	priv, err := release.LoadPrivateKey(s.key)
	if err != nil {
		return fail(stderr, "%v", err)
	}
	data, err := os.ReadFile(s.file)
	if err != nil {
		return fail(stderr, "ler %s: %v", s.file, err)
	}
	sig, digest := release.Sign(priv, s.component, s.version, s.goos, s.goarch, data)
	out, _ := json.MarshalIndent(map[string]any{
		"component": s.component, "version": s.version, "os": s.goos, "arch": s.goarch,
		"sha256": digest, "size_bytes": len(data), "signature": sig,
	}, "", "  ")
	_, _ = fmt.Fprintln(stdout, string(out))
	return 0
}

func cmdVerify(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("verify", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var s signFlags
	s.register(fs)
	signature := fs.String("signature", "", "assinatura (base64)")
	pubKey := fs.String("public-key", "", "chave pública (base64); padrão: a embutida")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if err := s.validate(); err != nil {
		return fail(stderr, "%v", err)
	}
	pub, err := release.PublicKey()
	if *pubKey != "" {
		pub, err = release.ParsePublicKey(*pubKey)
	}
	if err != nil {
		return fail(stderr, "%v", err)
	}
	data, err := os.ReadFile(s.file)
	if err != nil {
		return fail(stderr, "ler %s: %v", s.file, err)
	}
	if err := release.VerifySignature(pub, s.component, s.version, s.goos, s.goarch, data, *signature); err != nil {
		return fail(stderr, "%v", err)
	}
	_, _ = fmt.Fprintf(stdout, "OK: assinatura válida (%s %s %s/%s, sha256 %s)\n", s.component, s.version, s.goos, s.goarch, release.SHA256(data))
	return 0
}

func fail(stderr io.Writer, format string, a ...any) int {
	_, _ = fmt.Fprintf(stderr, "ERRO: "+format+"\n", a...)
	return 1
}
