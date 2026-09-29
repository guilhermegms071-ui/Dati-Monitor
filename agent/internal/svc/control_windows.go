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

// open opens a service with only the rights the operation needs (SC_MANAGER_CONNECT + the given mask):
// querying works for any user; starting/stopping needs the service account or an administrator.
func open(name string, access uint32) (*mgr.Service, func(), error) {
	scm, err := windows.OpenSCManager(nil, nil, windows.SC_MANAGER_CONNECT)
	if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		return nil, nil, ErrNoPermission
	}
	if err != nil {
		return nil, nil, fmt.Errorf("conectar ao gerenciador de serviços: %w", err)
	}
	h, err := windows.OpenService(scm, windows.StringToUTF16Ptr(name), access)
	if err != nil {
		_ = windows.CloseServiceHandle(scm)
		if errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST) {
			return nil, nil, fmt.Errorf("%s: %w", name, ErrNotInstalled)
		}
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			return nil, nil, ErrNoPermission
		}
		return nil, nil, fmt.Errorf("abrir serviço %s: %w", name, err)
	}
	s := &mgr.Service{Name: name, Handle: h}
	return s, func() {
		_ = s.Close()
		_ = windows.CloseServiceHandle(scm)
	}, nil
}

func normalize(st svc.State) string {
	switch st {
	case svc.Running:
		return StateRunning
	case svc.Stopped:
		return StateStopped
	case svc.StartPending, svc.ContinuePending:
		return StateStarting
	case svc.StopPending, svc.PausePending, svc.Paused:
		return StateStopping
	default:
		return StateUnknown
	}
}

// Query returns the state, process id and executable of a Windows service.
func Query(name string) (Status, error) {
	s, done, err := open(name, windows.SERVICE_QUERY_STATUS|windows.SERVICE_QUERY_CONFIG)
	if errors.Is(err, ErrNotInstalled) {
		return Status{State: StateNotInstalled}, nil
	}
	if err != nil {
		return Status{State: StateUnknown}, err
	}
	defer done()
	st, err := s.Query()
	if err != nil {
		return Status{State: StateUnknown}, fmt.Errorf("consultar serviço %s: %w", name, err)
	}
	out := Status{State: normalize(st.State), PID: int(st.ProcessId)}
	if cfg, err := s.Config(); err == nil {
		out.Exe = exeFromCommandLine(cfg.BinaryPathName)
	}
	return out, nil
}

// Start starts a Windows service (no-op if it is already running).
func Start(name string) error {
	s, done, err := open(name, windows.SERVICE_START|windows.SERVICE_QUERY_STATUS)
	if err != nil {
		return err
	}
	defer done()
	if err := s.Start(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			return ErrNoPermission
		}
		return fmt.Errorf("iniciar serviço %s: %w", name, err)
	}
	return nil
}

// Stop stops a Windows service and waits until it is stopped (or the timeout passes).
func Stop(name string, timeout time.Duration) error {
	s, done, err := open(name, windows.SERVICE_STOP|windows.SERVICE_QUERY_STATUS)
	if err != nil {
		return err
	}
	defer done()
	if _, err := s.Control(svc.Stop); err != nil && !errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			return ErrNoPermission
		}
		return fmt.Errorf("parar serviço %s: %w", name, err)
	}
	deadline := time.Now().Add(timeout)
	for {
		st, err := s.Query()
		if err != nil {
			return fmt.Errorf("consultar serviço %s: %w", name, err)
		}
		if st.State == svc.Stopped {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("serviço %s não parou em %s", name, timeout)
		}
		time.Sleep(300 * time.Millisecond)
	}
}

// Remove deletes a Windows service (it stays until its process exits, then disappears).
func Remove(name string) error {
	s, done, err := open(name, windows.DELETE)
	if err != nil {
		return err
	}
	defer done()
	if err := s.Delete(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_MARKED_FOR_DELETE) {
		return fmt.Errorf("remover serviço %s: %w", name, err)
	}
	return nil
}
