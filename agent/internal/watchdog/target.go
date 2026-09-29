package watchdog

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// Target is the agent as the watchdog controls it.
type Target interface {
	Status(ctx context.Context) (svc.Status, error)
	Start(ctx context.Context) error
	Stop(ctx context.Context) error
	Exe() (string, error)
	// Uninstall removes the agent service (for `uninstall`); a child process is just stopped.
	Uninstall(ctx context.Context) error
}

// ServiceTarget controls the agent's OS service (DatiMonitorAgent / systemd unit).
type ServiceTarget struct{ Name string }

// Status implements Target.
func (t ServiceTarget) Status(context.Context) (svc.Status, error) { return svc.Query(t.Name) }

// Start implements Target.
func (t ServiceTarget) Start(context.Context) error { return svc.Start(t.Name) }

// Stop implements Target.
func (t ServiceTarget) Stop(context.Context) error { return svc.Stop(t.Name, 30*time.Second) }

// Exe implements Target: the binary registered in the service.
func (t ServiceTarget) Exe() (string, error) {
	st, err := svc.Query(t.Name)
	if err != nil {
		return "", err
	}
	if st.Exe == "" {
		return "", fmt.Errorf("serviço %s sem executável registrado (%s)", t.Name, st.State)
	}
	return st.Exe, nil
}

// Uninstall implements Target.
func (t ServiceTarget) Uninstall(ctx context.Context) error {
	if err := t.Stop(ctx); err != nil && !errors.Is(err, svc.ErrNotInstalled) {
		return err
	}
	if err := svc.Remove(t.Name); err != nil && !errors.Is(err, svc.ErrNotInstalled) {
		return err
	}
	return nil
}

// ProcessTarget runs the agent as a child process (development, tests and scripts\chaos.ps1, where
// there is no administrator to install services). Same contract as the service.
type ProcessTarget struct {
	Path    string
	Args    []string
	LogPath string // saída do processo (anexada)

	mu     sync.Mutex
	cmd    *exec.Cmd
	exited chan struct{}
	logf   *os.File
}

// Status implements Target.
func (t *ProcessTarget) Status(context.Context) (svc.Status, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.cmd == nil || t.cmd.Process == nil {
		return svc.Status{State: svc.StateStopped, Exe: t.Path}, nil
	}
	select {
	case <-t.exited:
		return svc.Status{State: svc.StateStopped, Exe: t.Path}, nil
	default:
		return svc.Status{State: svc.StateRunning, PID: t.cmd.Process.Pid, Exe: t.Path}, nil
	}
}

// Start implements Target (no-op if the process is alive).
func (t *ProcessTarget) Start(context.Context) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.cmd != nil {
		select {
		case <-t.exited:
		default:
			return nil
		}
	}
	cmd := exec.Command(t.Path, t.Args...) //nolint:gosec // G204: binário do agente configurado no watchdog
	if t.LogPath != "" {
		f, err := os.OpenFile(t.LogPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600) //nolint:gosec // G304: caminho do produto
		if err != nil {
			return fmt.Errorf("abrir log do coletor: %w", err)
		}
		cmd.Stdout, cmd.Stderr = f, f
		t.logf = f
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("iniciar o coletor (%s): %w", t.Path, err)
	}
	exited := make(chan struct{})
	logf := t.logf
	go func() {
		_ = cmd.Wait()
		if logf != nil {
			_ = logf.Close()
		}
		close(exited)
	}()
	t.cmd, t.exited = cmd, exited
	return nil
}

// Stop implements Target: kills the process and waits for it (the agent's queue survives a kill: every
// reading is committed to SQLite before it is sent).
func (t *ProcessTarget) Stop(context.Context) error {
	t.mu.Lock()
	cmd, exited := t.cmd, t.exited
	t.mu.Unlock()
	if cmd == nil || cmd.Process == nil {
		return nil
	}
	select {
	case <-exited:
		return nil
	default:
	}
	if err := cmd.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
		return fmt.Errorf("encerrar o coletor: %w", err)
	}
	select {
	case <-exited:
		return nil
	case <-time.After(15 * time.Second):
		return errors.New("o coletor não encerrou em 15 s")
	}
}

// Exe implements Target.
func (t *ProcessTarget) Exe() (string, error) { return t.Path, nil }

// Uninstall implements Target.
func (t *ProcessTarget) Uninstall(ctx context.Context) error { return t.Stop(ctx) }
