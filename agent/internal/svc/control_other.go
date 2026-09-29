//go:build !windows

package svc

import (
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func systemctlShow(name string, props ...string) (map[string]string, error) {
	args := []string{"show", name}
	for _, p := range props {
		args = append(args, "-p", p)
	}
	out, err := exec.Command("systemctl", args...).Output() //nolint:gosec // G204: nome do serviço vem do produto
	if err != nil {
		return nil, fmt.Errorf("systemctl show %s: %w", name, err)
	}
	values := map[string]string{}
	for _, line := range strings.Split(string(out), "\n") {
		if k, v, ok := strings.Cut(line, "="); ok {
			values[k] = v
		}
	}
	return values, nil
}

// Query returns the state, main PID and executable of a systemd unit.
func Query(name string) (Status, error) {
	v, err := systemctlShow(name, "LoadState", "ActiveState", "MainPID", "ExecStart")
	if err != nil {
		return Status{State: StateUnknown}, err
	}
	if v["LoadState"] == "not-found" {
		return Status{State: StateNotInstalled}, nil
	}
	out := Status{State: StateUnknown, Exe: execStartPath(v["ExecStart"])}
	out.PID, _ = strconv.Atoi(v["MainPID"])
	switch v["ActiveState"] {
	case "active", "reloading":
		out.State = StateRunning
	case "inactive", "failed":
		out.State = StateStopped
	case "activating":
		out.State = StateStarting
	case "deactivating":
		out.State = StateStopping
	}
	return out, nil
}

func systemctl(action, name string) error {
	if out, err := exec.Command("systemctl", action, name).CombinedOutput(); err != nil { //nolint:gosec // G204: idem
		return fmt.Errorf("systemctl %s %s: %w: %s", action, name, err, strings.TrimSpace(string(out)))
	}
	return nil
}

// Start starts a systemd unit.
func Start(name string) error { return systemctl("start", name) }

// Stop stops a systemd unit (systemctl waits for it; the timeout comes from TimeoutStopSec).
func Stop(name string, _ time.Duration) error { return systemctl("stop", name) }

// Remove disables a systemd unit (the unit file is removed by the uninstaller).
func Remove(name string) error { return systemctl("disable", name) }
