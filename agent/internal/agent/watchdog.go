package agent

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/commands"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/release"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// Mutual watch (PROMPT 5.1): the agent checks the watchdog service every 60 s and starts it if stopped.
const (
	WatchdogCheckInterval = 60 * time.Second
	HealthLoopWatchdog    = "watchdog_watch"
	WatchdogUpdateWait    = 2 * time.Minute
)

func (a *Agent) watchdogLoop(ctx context.Context) {
	every := a.WatchdogCheckEvery
	if every <= 0 {
		every = WatchdogCheckInterval
	}
	a.Health.Register(HealthLoopWatchdog, every)
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		a.checkWatchdog(ctx)
		a.Health.Beat(HealthLoopWatchdog)
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func (a *Agent) checkWatchdog(ctx context.Context) {
	if a.WatchdogService == nil {
		a.watchdogState.Store(protocol.ServiceUnknown)
		return
	}
	st, err := a.WatchdogService.Status(ctx)
	state := st.State
	switch {
	case err != nil:
		state = protocol.ServiceUnknown
		a.noteWatchdog(state, "consultar o serviço do watchdog", err)
	case st.State == svc.StateStopped:
		a.Log.Warn("o serviço do watchdog estava parado; iniciando (vigilância mútua)")
		if err := a.WatchdogService.Start(ctx); err != nil {
			a.Log.Error("iniciar o serviço do watchdog", "erro", err)
			state = protocol.ServiceStopped
		} else {
			state = protocol.ServiceStarting
		}
	case st.State == svc.StateStopping:
		state = protocol.ServiceStopped
	case st.State == svc.StateNotInstalled:
		a.noteWatchdog(state, "watchdog não instalado como serviço neste PC", nil)
	}
	a.watchdogState.Store(state)
}

// noteWatchdog logs a watchdog condition only when it changes (the loop runs every minute).
func (a *Agent) noteWatchdog(state, msg string, err error) {
	prev, _ := a.watchdogState.Load().(string)
	if prev == state {
		return
	}
	if err != nil {
		a.Log.Error(msg, "erro", err)
		return
	}
	a.Log.Warn(msg)
}

// WatchdogState is the last known state of the watchdog service (for heartbeats).
func (a *Agent) WatchdogState() string {
	s, _ := a.watchdogState.Load().(string)
	if s == "" {
		return protocol.ServiceUnknown
	}
	return s
}

func (a *Agent) watchdogHealth(ctx context.Context) (*health.Report, error) {
	addr := a.WatchdogHealth
	if addr == "" {
		return nil, errors.New("endereço do /health do watchdog não configurado")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+addr+"/health", nil)
	if err != nil {
		return nil, err
	}
	resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
	if err != nil {
		return nil, fmt.Errorf("/health do watchdog sem resposta: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	var rep health.Report
	if err := json.NewDecoder(resp.Body).Decode(&rep); err != nil {
		return nil, fmt.Errorf("/health do watchdog inválido: %w", err)
	}
	return &rep, nil
}

// waitWatchdogVersion: the new watchdog runs `version` and already reported to the server after `since`.
func (a *Agent) waitWatchdogVersion(ctx context.Context, version string, since time.Time) error {
	var last error
	for {
		rep, err := a.watchdogHealth(ctx)
		switch {
		case err != nil:
			last = err
		default:
			v, _ := rep.Info["version"].(string)
			at, _ := rep.Info["last_report_at"].(string)
			reported, perr := time.Parse(time.RFC3339Nano, at)
			switch {
			case v != version:
				last = fmt.Errorf("watchdog rodando a versão %q", v)
			case perr != nil || !reported.After(since):
				last = errors.New("watchdog ainda sem heartbeat aceito pelo servidor")
			default:
				return nil
			}
		}
		select {
		case <-ctx.Done():
			return last
		case <-time.After(2 * time.Second):
		}
	}
}

// cmdUpdate updates the watchdog (the inverse process of PROMPT 5.2: the watchdog updates the agent).
func (a *Agent) cmdUpdate(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	p, err := decode[protocol.UpdateParams](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	if p.Component != "watchdog" {
		return commands.Result{}, errors.New("o coletor atualiza só o watchdog; o coletor é atualizado pelo watchdog")
	}
	if a.WatchdogService == nil {
		return commands.Result{}, errors.New("controle do serviço do watchdog indisponível")
	}
	st, err := a.WatchdogService.Status(ctx)
	if err != nil {
		return commands.Result{}, fmt.Errorf("consultar o watchdog: %w", err)
	}
	if st.State == svc.StateNotInstalled {
		return commands.Result{}, errors.New("o watchdog não está instalado como serviço neste PC")
	}
	key := a.ReleaseKey
	if len(key) == 0 {
		if key, err = release.PublicKey(); err != nil {
			return commands.Result{}, err
		}
	}
	current := ""
	if rep, err := a.watchdogHealth(ctx); err == nil {
		current, _ = rep.Info["version"].(string)
	}
	inst := &release.Installer{
		Service:   a.WatchdogService,
		StateDir:  filepath.Join(a.Dir, "updates", "watchdog"),
		PublicKey: key,
		Download:  a.Client.Download,
		Healthy:   a.waitWatchdogVersion,
		Wait:      WatchdogUpdateWait,
		Log:       a.Log,
	}
	progress(fmt.Sprintf("atualizando o watchdog %s → %s", current, p.Version))
	res, err := inst.Update(ctx, p, current)
	return commands.Result{Data: res.Map()}, err
}
