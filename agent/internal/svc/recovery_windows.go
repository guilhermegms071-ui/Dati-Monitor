//go:build windows

package svc

import (
	"errors"
	"fmt"
	"time"

	"golang.org/x/sys/windows"
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

// ServiceState returns the normalized state of a service (running, stopped, not_installed, …); querying
// needs no special rights.
func ServiceState(name string) (string, error) {
	st, err := Query(name)
	return st.State, err
}

// ErrNotInstalled means the service does not exist on this machine.
var ErrNotInstalled = errors.New("serviço não está instalado neste PC")

// ErrNoPermission means the process may not control services (not running as the service account).
var ErrNoPermission = errors.New("sem permissão para controlar serviços do Windows (o coletor precisa rodar como serviço do sistema)")

// RestartService stops (waiting up to 30 s) and starts a Windows service.
func RestartService(name string) error {
	m, err := mgr.Connect()
	if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		return ErrNoPermission
	}
	if err != nil {
		return fmt.Errorf("conectar ao gerenciador de serviços: %w", err)
	}
	defer func() { _ = m.Disconnect() }()
	s, err := m.OpenService(name)
	if err != nil {
		if errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST) {
			return fmt.Errorf("%s: %w", name, ErrNotInstalled)
		}
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			return ErrNoPermission
		}
		return fmt.Errorf("abrir serviço %s: %w", name, err)
	}
	defer func() { _ = s.Close() }()
	st, err := s.Query()
	if err != nil {
		return fmt.Errorf("consultar serviço %s: %w", name, err)
	}
	if st.State != svc.Stopped {
		if _, err := s.Control(svc.Stop); errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			return ErrNoPermission
		} else if err != nil && !errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
			return fmt.Errorf("parar serviço %s: %w", name, err)
		}
		deadline := time.Now().Add(30 * time.Second)
		for {
			st, err = s.Query()
			if err != nil {
				return fmt.Errorf("consultar serviço %s: %w", name, err)
			}
			if st.State == svc.Stopped {
				break
			}
			if time.Now().After(deadline) {
				return fmt.Errorf("serviço %s não parou em 30 s", name)
			}
			time.Sleep(300 * time.Millisecond)
		}
	}
	if err := s.Start(); err != nil {
		return fmt.Errorf("iniciar serviço %s: %w", name, err)
	}
	return nil
}
