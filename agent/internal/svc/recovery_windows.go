//go:build windows

package svc

import (
	"fmt"
	"time"

	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

// RecoveryActions are the SCM actions of PROMPT 4.1: restart after 5 s, 5 s, 30 s.
var RecoveryActions = []mgr.RecoveryAction{
	{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
	{Type: mgr.ServiceRestart, Delay: 5 * time.Second},
	{Type: mgr.ServiceRestart, Delay: 30 * time.Second},
}

// RecoveryResetSeconds zeroes the failure counter after 1 day.
const RecoveryResetSeconds = 86400

// ConfigureRecovery applies the recovery actions (also for non-crash failures, e.g. exit code != 0).
func ConfigureRecovery(name string) error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("conectar ao gerenciador de serviços: %w", err)
	}
	defer func() { _ = m.Disconnect() }()
	s, err := m.OpenService(name)
	if err != nil {
		return fmt.Errorf("abrir serviço %s: %w", name, err)
	}
	defer func() { _ = s.Close() }()
	if err := s.SetRecoveryActions(RecoveryActions, RecoveryResetSeconds); err != nil {
		return fmt.Errorf("ações de recuperação: %w", err)
	}
	if err := s.SetRecoveryActionsOnNonCrashFailures(true); err != nil {
		return fmt.Errorf("recuperação em falhas sem crash: %w", err)
	}
	cfg, err := s.Config()
	if err != nil {
		return err
	}
	cfg.DelayedAutoStart = true
	cfg.StartType = mgr.StartAutomatic
	return s.UpdateConfig(cfg)
}

// RecoveryReport describes the configured recovery actions (used by status/diagnostics and tests).
func RecoveryReport(name string) (string, error) {
	m, err := mgr.Connect()
	if err != nil {
		return "", err
	}
	defer func() { _ = m.Disconnect() }()
	s, err := m.OpenService(name)
	if err != nil {
		return "", err
	}
	defer func() { _ = s.Close() }()
	acts, err := s.RecoveryActions()
	if err != nil {
		return "", err
	}
	reset, err := s.ResetPeriod()
	if err != nil {
		return "", err
	}
	out := ""
	for i, a := range acts {
		out += fmt.Sprintf("%d:%d:%s ", i+1, a.Type, a.Delay)
	}
	return fmt.Sprintf("%sreset=%ds", out, reset), nil
}

// ServiceState returns the SCM state of a service ("running", "stopped", ...).
func ServiceState(name string) (string, error) {
	m, err := mgr.Connect()
	if err != nil {
		return "", err
	}
	defer func() { _ = m.Disconnect() }()
	s, err := m.OpenService(name)
	if err != nil {
		return "not_installed", nil //nolint:nilerr // serviço ausente é um estado válido
	}
	defer func() { _ = s.Close() }()
	st, err := s.Query()
	if err != nil {
		return "", err
	}
	switch st.State {
	case svc.Running:
		return "running", nil
	case svc.Stopped:
		return "stopped", nil
	case svc.StartPending:
		return "start_pending", nil
	case svc.StopPending:
		return "stop_pending", nil
	default:
		return fmt.Sprintf("state_%d", st.State), nil
	}
}
