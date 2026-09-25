//go:build !windows

package svc

import (
	"errors"
	"fmt"
	"os/exec"
	"strings"
)

// ConfigureRecovery does nothing on Linux: the restart policy lives in the systemd unit (Restart=always, …).
func ConfigureRecovery(string) error { return nil }

// RecoveryReport describes the systemd restart policy of the unit.
func RecoveryReport(name string) (string, error) {
	out, err := exec.Command("systemctl", "show", name, "-p", "Restart", "-p", "RestartUSec", "-p", "WatchdogUSec").Output() //nolint:gosec // G204: nome do serviço vem do produto
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(strings.ReplaceAll(string(out), "\n", " ")), nil
}

// ServiceState returns the systemd ActiveState of a unit.
func ServiceState(name string) (string, error) {
	out, err := exec.Command("systemctl", "show", name, "-p", "ActiveState", "--value").Output() //nolint:gosec // G204: idem
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

// ErrNotInstalled means the service does not exist on this machine.
var ErrNotInstalled = errors.New("serviço não está instalado neste computador")

// RestartService restarts a systemd unit.
func RestartService(name string) error {
	state, err := exec.Command("systemctl", "show", name, "-p", "LoadState", "--value").Output() //nolint:gosec // G204: nome do serviço vem do produto
	if err != nil {
		return fmt.Errorf("consultar %s: %w", name, err)
	}
	if strings.TrimSpace(string(state)) == "not-found" {
		return fmt.Errorf("%s: %w", name, ErrNotInstalled)
	}
	if out, err := exec.Command("systemctl", "restart", name).CombinedOutput(); err != nil { //nolint:gosec // G204: idem
		return fmt.Errorf("systemctl restart %s: %w: %s", name, err, strings.TrimSpace(string(out)))
	}
	return nil
}
