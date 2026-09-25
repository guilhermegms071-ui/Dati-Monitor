// Package svc installs and runs the dm-* binaries as OS services (kardianos/service): Windows
// service with delayed automatic start and SCM recovery actions (restart after 5 s, 5 s, 30 s; reset
// after 1 day), or a systemd unit with Restart=always, RestartSec=5, WatchdogSec=60 (PROMPT 4.1).
package svc

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/kardianos/service"
)

// Definition describes one service.
type Definition struct {
	Name        string
	DisplayName string
	Description string
	Arguments   []string // argumentos passados ao binário quando roda como serviço
	// Dependencies for systemd (e.g. network-online.target).
	Dependencies []string
}

// Config builds the kardianos configuration (LocalSystem on Windows; see docs for the reason).
func (d Definition) Config() *service.Config {
	deps := d.Dependencies
	if len(deps) == 0 {
		deps = []string{"Requires=network-online.target", "After=network-online.target"}
	}
	return &service.Config{
		Name:         d.Name,
		DisplayName:  d.DisplayName,
		Description:  d.Description,
		Arguments:    d.Arguments,
		Dependencies: deps,
		Option: service.KeyValue{
			"DelayedAutoStart": true,
			"StartType":        "automatic",
			"OnFailure":        "restart",
			"SystemdScript":    systemdUnit,
			"Restart":          "always",
		},
	}
}

// systemdUnit is the unit template (kardianos fields) with the restart/watchdog policy.
const systemdUnit = `[Unit]
Description={{.Description}}
ConditionFileIsExecutable={{.Path|cmdEscape}}
{{range $i, $dep := .Dependencies}}{{$dep}}
{{end}}
[Service]
Type=notify
NotifyAccess=main
StartLimitIntervalSec=0
ExecStart={{.Path|cmdEscape}}{{range .Arguments}} {{.|cmd}}{{end}}
{{if .WorkingDirectory}}WorkingDirectory={{.WorkingDirectory|cmdEscape}}{{end}}
{{if .UserName}}User={{.UserName}}{{end}}
Restart=always
RestartSec=5
WatchdogSec=60
TimeoutStopSec=30
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
`

// Runner adapts a context-based run function to kardianos' Start/Stop.
type Runner struct {
	Run func(ctx context.Context) error

	mu     sync.Mutex
	cancel context.CancelFunc
	done   chan error
}

// Start implements service.Interface (must not block).
func (r *Runner) Start(service.Service) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	ctx, cancel := context.WithCancel(context.Background())
	r.cancel = cancel
	r.done = make(chan error, 1)
	go func() { r.done <- r.Run(ctx) }()
	return nil
}

// Stop implements service.Interface: cancels and waits up to 25 s.
func (r *Runner) Stop(service.Service) error {
	r.mu.Lock()
	cancel, done := r.cancel, r.done
	r.mu.Unlock()
	if cancel == nil {
		return nil
	}
	cancel()
	select {
	case err := <-done:
		if err != nil && !errors.Is(err, context.Canceled) {
			return err
		}
		return nil
	case <-time.After(25 * time.Second):
		return errors.New("o serviço não terminou em 25 s")
	}
}

// New returns the kardianos service for the definition and runner.
func New(d Definition, r *Runner) (service.Service, error) {
	s, err := service.New(r, d.Config())
	if err != nil {
		return nil, fmt.Errorf("serviço %s: %w", d.Name, err)
	}
	return s, nil
}

// Install installs the service and configures the recovery policy.
func Install(s service.Service, d Definition) error {
	if err := s.Install(); err != nil {
		return fmt.Errorf("instalar serviço %s (precisa de terminal como administrador): %w", d.Name, err)
	}
	if err := ConfigureRecovery(d.Name); err != nil {
		return fmt.Errorf("serviço instalado, mas falhou ao configurar a recuperação automática: %w", err)
	}
	return nil
}

// Interactive reports whether we run from a console (not under the service manager).
func Interactive() bool { return service.Interactive() }
