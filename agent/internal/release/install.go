package release

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// Service is the process whose binary is replaced: the agent (updated by the watchdog) or the watchdog
// (updated by the agent).
type Service interface {
	Stop(ctx context.Context) error
	Start(ctx context.Context) error
	Exe() (string, error)
}

// Installer runs the update flow of PROMPT 5.2: download → verify hash and signature → stop → keep the
// current binary as `previous` → replace → start → wait until healthy AND seen by the server (2 min) →
// otherwise roll back to `previous` automatically and report the failure.
type Installer struct {
	Service   Service
	StateDir  string // guarda previous(.exe) e previous.version
	PublicKey ed25519.PublicKey
	Download  func(ctx context.Context, path string, limit int64) ([]byte, error)
	// Healthy returns nil once `version` runs healthy and reached the server after `since`.
	Healthy func(ctx context.Context, version string, since time.Time) error
	Wait    time.Duration
	Log     *slog.Logger
}

// Result of an update or rollback.
type Result struct {
	From       string  `json:"from"`
	To         string  `json:"to"`
	RolledBack bool    `json:"rolled_back"`
	Seconds    float64 `json:"seconds"`
}

// Map is the command result sent to the portal.
func (r Result) Map() map[string]any {
	return map[string]any{"from": r.From, "to": r.To, "rolled_back": r.RolledBack, "seconds": r.Seconds}
}

func (i *Installer) previousPath(exe string) string {
	return filepath.Join(i.StateDir, "previous"+filepath.Ext(exe))
}

func (i *Installer) versionPath() string { return filepath.Join(i.StateDir, "previous.version") }

// PreviousVersion is the version kept for rollback ("" = none).
func (i *Installer) PreviousVersion() string {
	raw, err := os.ReadFile(i.versionPath())
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(raw))
}

// Update installs the release described by p (current = version running now).
func (i *Installer) Update(ctx context.Context, p protocol.UpdateParams, current string) (Result, error) {
	started := time.Now()
	res := Result{From: current, To: p.Version}
	limit := p.SizeBytes
	if limit <= 0 {
		limit = 80 << 20
	}
	data, err := i.Download(ctx, p.URL, limit)
	if err != nil {
		return res, fmt.Errorf("baixar a versão %s: %w", p.Version, err)
	}
	if err := Verify(i.PublicKey, p, data); err != nil {
		return res, fmt.Errorf("versão %s recusada: %w (nada foi alterado)", p.Version, err)
	}
	exe, err := i.Service.Exe()
	if err != nil {
		return res, fmt.Errorf("localizar o executável atual: %w", err)
	}
	if err := os.MkdirAll(i.StateDir, 0o750); err != nil {
		return res, err
	}
	prev := i.previousPath(exe)
	i.Log.Info("atualização: parando para trocar o binário", "de", current, "para", p.Version, "exe", exe)
	if err := i.Service.Stop(ctx); err != nil {
		return res, fmt.Errorf("parar para atualizar: %w", err)
	}
	if err := copyFile(exe, prev); err != nil {
		i.startAgain(ctx)
		return res, fmt.Errorf("guardar a versão atual para rollback: %w", err)
	}
	if err := writeAtomic(i.versionPath(), []byte(current+"\n")); err != nil {
		i.startAgain(ctx)
		return res, fmt.Errorf("guardar a versão atual para rollback: %w", err)
	}
	if err := replace(exe, data); err != nil {
		i.startAgain(ctx)
		return res, fmt.Errorf("gravar o novo executável: %w", err)
	}
	cause := i.startAndWait(ctx, p.Version)
	res.Seconds = time.Since(started).Seconds()
	if cause == nil {
		i.Log.Info("atualização concluída", "versao", p.Version, "segundos", res.Seconds)
		return res, nil
	}
	i.Log.Error("nova versão não ficou saudável; rollback automático", "versao", p.Version, "erro", cause)
	if rbErr := i.restore(ctx, exe, prev, current); rbErr != nil {
		return res, fmt.Errorf("versão %s não ficou saudável (%w) e o rollback para %s falhou: %w", p.Version, cause, current, rbErr)
	}
	res.To, res.RolledBack = current, true
	res.Seconds = time.Since(started).Seconds()
	return res, fmt.Errorf("versão %s não ficou saudável em %s (%w); rollback para %s feito", p.Version, i.Wait, cause, current)
}

// Rollback swaps the running binary with the kept `previous` one (a second rollback swaps back).
func (i *Installer) Rollback(ctx context.Context, current string) (Result, error) {
	started := time.Now()
	target := i.PreviousVersion()
	res := Result{From: current, To: target}
	exe, err := i.Service.Exe()
	if err != nil {
		return res, fmt.Errorf("localizar o executável atual: %w", err)
	}
	prev := i.previousPath(exe)
	if target == "" {
		return res, errors.New("não há versão anterior guardada")
	}
	if _, err := os.Stat(prev); err != nil {
		return res, fmt.Errorf("binário da versão anterior ausente: %w", err)
	}
	if err := i.Service.Stop(ctx); err != nil {
		return res, fmt.Errorf("parar para voltar a versão: %w", err)
	}
	swap := prev + ".swap"
	if err := copyFile(exe, swap); err != nil {
		i.startAgain(ctx)
		return res, err
	}
	if err := copyFile(prev, exe); err != nil {
		i.startAgain(ctx)
		return res, fmt.Errorf("restaurar a versão anterior: %w", err)
	}
	if err := os.Rename(swap, prev); err != nil {
		i.Log.Error("guardar a versão substituída", "erro", err)
	}
	if err := writeAtomic(i.versionPath(), []byte(current+"\n")); err != nil {
		i.Log.Error("guardar a versão substituída", "erro", err)
	}
	if err := i.startAndWait(ctx, target); err != nil {
		return res, fmt.Errorf("a versão %s voltou, mas não ficou saudável: %w", target, err)
	}
	res.Seconds = time.Since(started).Seconds()
	return res, nil
}

// startAgain restarts the service after an aborted update/rollback (the old binary is still in place).
func (i *Installer) startAgain(ctx context.Context) {
	if err := i.Service.Start(ctx); err != nil {
		i.Log.Error("reiniciar depois da falha na atualização", "erro", err)
	}
}

func (i *Installer) startAndWait(ctx context.Context, version string) error {
	since := time.Now()
	if err := i.Service.Start(ctx); err != nil {
		return fmt.Errorf("iniciar: %w", err)
	}
	wctx, cancel := context.WithTimeout(ctx, i.Wait)
	defer cancel()
	return i.Healthy(wctx, version, since)
}

func (i *Installer) restore(ctx context.Context, exe, prev, version string) error {
	if err := i.Service.Stop(ctx); err != nil {
		i.Log.Error("parar a versão com defeito", "erro", err)
	}
	if err := copyFile(prev, exe); err != nil {
		return fmt.Errorf("copiar a versão anterior de volta: %w", err)
	}
	return i.startAndWait(ctx, version)
}

func copyFile(src, dst string) error {
	in, err := os.Open(src) //nolint:gosec // G304: caminhos do próprio produto
	if err != nil {
		return err
	}
	defer func() { _ = in.Close() }()
	tmp := dst + ".tmp"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755) //nolint:gosec // G302/G304: executável do produto
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		_ = out.Close()
		return err
	}
	if err := out.Close(); err != nil {
		return err
	}
	return os.Rename(tmp, dst)
}

func replace(exe string, data []byte) error {
	tmp := exe + ".new"
	if err := os.WriteFile(tmp, data, 0o755); err != nil { //nolint:gosec // G306: executável precisa de permissão de execução
		return err
	}
	return os.Rename(tmp, exe)
}

func writeAtomic(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
