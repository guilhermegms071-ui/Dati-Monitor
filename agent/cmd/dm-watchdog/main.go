// Command dm-watchdog: vigia do coletor, atualização e rollback (PROMPT 5.1/5.2).
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/cli"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/logx"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
	"github.com/daticopy/dati-monitor/agent/internal/watchdog"
)

func definition(dataDir string) svc.Definition {
	args := []string{"service"}
	if dataDir != "" && dataDir != product.DataDir(runtime.GOOS) {
		args = append(args, "--data-dir", dataDir)
	}
	return svc.Definition{
		Name:        product.WatchdogServiceName(),
		DisplayName: product.Name + " — Vigia do coletor",
		Description: "Mantém o coletor do " + product.Name + " funcionando, atualiza e volta versões.",
		Arguments:   args,
	}
}

func main() {
	app := cli.App{Binary: "dm-watchdog", Description: "vigia do coletor, atualização e rollback", Commands: map[string]cli.Command{
		"run":       {Summary: "executa o watchdog no terminal (Ctrl+C encerra)", Run: cmdRun},
		"service":   {Summary: "uso interno: executado pelo gerenciador de serviços", Run: cmdService},
		"install":   {Summary: "instala o serviço (terminal como administrador)", Run: serviceControl("install")},
		"uninstall": {Summary: "remove o serviço (terminal como administrador)", Run: serviceControl("uninstall")},
		"start":     {Summary: "inicia o serviço", Run: serviceControl("start")},
		"stop":      {Summary: "para o serviço", Run: serviceControl("stop")},
		"status":    {Summary: "mostra o estado do watchdog e do coletor", Run: cmdStatus},
	}}
	os.Exit(app.Main(os.Args[1:], os.Stdout, os.Stderr))
}

func fail(stderr io.Writer, format string, a ...any) int {
	_, _ = fmt.Fprintf(stderr, "ERRO: "+format+"\n", a...)
	return 1
}

// runOptions select how the agent is controlled: its service (default) or a child process
// (--agent-exe, for development and scripts\chaos.ps1 without administrator).
type runOptions struct {
	dataDir     string
	agentExe    string
	agentArgs   string
	healthAddr  string
	checkEvery  time.Duration
	reportEvery time.Duration
	startGrace  time.Duration
	updateWait  time.Duration
}

func (o *runOptions) flags(fs *flag.FlagSet) {
	fs.StringVar(&o.dataDir, "data-dir", "", "pasta de dados do coletor")
	fs.StringVar(&o.agentExe, "agent-exe", "", "desenvolvimento: roda o coletor como processo filho (em vez do serviço)")
	fs.StringVar(&o.agentArgs, "agent-args", "", "argumentos do processo filho (padrão: run --data-dir <pasta>)")
	fs.StringVar(&o.healthAddr, "health-addr", watchdog.HealthAddr, "endereço do /health do próprio watchdog")
	fs.DurationVar(&o.checkEvery, "check-every", watchdog.DefaultCheckEvery, "intervalo de checagem do coletor")
	fs.DurationVar(&o.reportEvery, "report-every", watchdog.DefaultReportEvery, "intervalo do heartbeat ao servidor")
	fs.DurationVar(&o.startGrace, "start-grace", watchdog.DefaultStartGrace, "tempo de partida do coletor antes de cobrar o /health")
	fs.DurationVar(&o.updateWait, "update-wait", watchdog.DefaultUpdateWait, "prazo para a nova versão ficar saudável")
}

func cmdRun(args []string, _, stderr io.Writer) int {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var o runOptions
	o.flags(fs)
	if err := fs.Parse(args); err != nil {
		return 2
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := runWatchdog(ctx, o, true); err != nil {
		return fail(stderr, "%v", err)
	}
	return 0
}

func cmdService(args []string, _, stderr io.Writer) int {
	fs := flag.NewFlagSet("service", flag.ContinueOnError)
	fs.SetOutput(stderr)
	var o runOptions
	o.flags(fs)
	if err := fs.Parse(args); err != nil {
		return 2
	}
	runner := &svc.Runner{Run: func(ctx context.Context) error { return runWatchdog(ctx, o, false) }}
	s, err := svc.New(definition(o.dataDir), runner)
	if err != nil {
		return fail(stderr, "%v", err)
	}
	if err := s.Run(); err != nil {
		return fail(stderr, "%v", err)
	}
	return 0
}

func runWatchdog(ctx context.Context, o runOptions, console bool) error {
	dir := config.DataDir(o.dataDir)
	if err := config.EnsureDirs(dir); err != nil {
		return err
	}
	lg, closer := logx.New(logx.Options{Dir: filepath.Join(dir, "logs"), Name: "watchdog", Level: os.Getenv("DM_LOG_LEVEL"), Console: console})
	defer func() { _ = closer.Close() }()
	if err := osinfo.CheckSupported(); err != nil {
		lg.Error("sistema não suportado", "erro", err)
		return err
	}
	local, err := config.Load(dir)
	if err != nil {
		lg.Error("coletor não cadastrado neste PC", "erro", err)
		return err
	}
	sec, err := secret.Load(dir)
	if err != nil {
		lg.Error("credencial do coletor ilegível", "erro", err)
		return err
	}
	client, err := api.New(api.Options{ServerURL: local.ServerURL, InsecureDev: local.InsecureDev, InsecureLAN: local.InsecureLAN, ProxyURL: local.ProxyURL},
		local.AgentID, secret.DeriveKey(sec))
	if err != nil {
		return err
	}
	var target watchdog.Target = watchdog.ServiceTarget{Name: product.AgentServiceName()}
	if o.agentExe != "" {
		agentArgs := []string{"run", "--data-dir", dir}
		if strings.TrimSpace(o.agentArgs) != "" {
			agentArgs = strings.Fields(o.agentArgs)
		}
		target = &watchdog.ProcessTarget{Path: o.agentExe, Args: agentArgs, LogPath: filepath.Join(dir, "logs", "agent-process.log")}
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	agentHealth := local.HealthAddr
	if agentHealth == "" {
		agentHealth = health.DefaultAddr
	}
	reg := health.NewRegistry()
	w, err := watchdog.New(watchdog.Options{
		DataDir:     dir,
		AgentHealth: agentHealth,
		Client:      client,
		Target:      target,
		Log:         lg,
		Version:     buildinfo.Version,
		Health:      reg,
		CheckEvery:  o.checkEvery,
		ReportEvery: o.reportEvery,
		StartGrace:  o.startGrace,
		UpdateWait:  o.updateWait,
		OnUninstall: func() {
			if o.agentExe == "" {
				if err := svc.Remove(product.WatchdogServiceName()); err != nil {
					lg.Error("remover o serviço do watchdog", "erro", err)
				}
			}
			lg.Warn("desinstalado pelo portal: watchdog encerrando")
			cancel()
		},
	})
	if err != nil {
		lg.Error("watchdog não pôde iniciar", "erro", err)
		return err
	}
	go func() {
		if err := health.Serve(ctx, o.healthAddr, reg); err != nil {
			lg.Error("endpoint de saúde do watchdog", "endereco", o.healthAddr, "erro", err)
		}
	}()
	if o.agentExe != "" {
		if err := target.Start(ctx); err != nil {
			lg.Error("iniciar o coletor", "erro", err)
		}
		defer func() {
			if err := target.Stop(context.Background()); err != nil {
				lg.Error("encerrar o coletor", "erro", err)
			}
		}()
	}
	go followServer(ctx, dir, local.ServerURL, client, lg)
	return w.Run(ctx)
}

// followServer keeps the watchdog on the same server as the collector: after a "Mudar endereço do
// servidor" (validated and saved by the collector), the next check switches the watchdog's channel too.
func followServer(ctx context.Context, dir, current string, client *api.Client, lg *slog.Logger) {
	t := time.NewTicker(followEvery)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		local, err := config.Load(dir)
		if err != nil || local.ServerURL == "" || local.ServerURL == current {
			continue
		}
		opts := api.Options{ServerURL: local.ServerURL, InsecureDev: local.InsecureDev, InsecureLAN: local.InsecureLAN, ProxyURL: local.ProxyURL}
		if err := client.SwitchServer(opts); err != nil {
			lg.Error("seguir o novo endereço do servidor", "erro", err)
			continue
		}
		lg.Info("watchdog usando o novo servidor", "de", current, "para", local.ServerURL)
		current = local.ServerURL
	}
}

// followEvery is how often the watchdog rereads the collector configuration (server address).
var followEvery = 30 * time.Second

func serviceControl(action string) func([]string, io.Writer, io.Writer) int {
	return func(args []string, stdout, stderr io.Writer) int {
		fs := flag.NewFlagSet(action, flag.ContinueOnError)
		fs.SetOutput(stderr)
		dataDir := fs.String("data-dir", "", "pasta de dados do coletor")
		if err := fs.Parse(args); err != nil {
			return 2
		}
		def := definition(*dataDir)
		s, err := svc.New(def, &svc.Runner{Run: func(context.Context) error { return nil }})
		if err != nil {
			return fail(stderr, "%v", err)
		}
		switch action {
		case "install":
			if err := osinfo.CheckSupported(); err != nil {
				return fail(stderr, "%v", err)
			}
			if _, err := config.Load(config.DataDir(*dataDir)); err != nil {
				return fail(stderr, "cadastre o coletor antes (dm-agent enroll): %v", err)
			}
			err = svc.Install(s, def)
		case "uninstall":
			_ = s.Stop()
			err = s.Uninstall()
		case "start":
			err = svc.Start(def.Name) // já rodando = sucesso (o coletor sobe o watchdog sozinho, vigia mútua)
		case "stop":
			err = s.Stop()
		}
		if err != nil {
			return fail(stderr, "%s do serviço %s: %v", action, def.Name, err)
		}
		_, _ = fmt.Fprintf(stdout, "OK: %s do serviço %s\n", action, def.Name)
		return 0
	}
}

func cmdStatus(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("status", flag.ContinueOnError)
	fs.SetOutput(stderr)
	addr := fs.String("health-addr", watchdog.HealthAddr, "endereço do /health do watchdog")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	for _, name := range []string{product.WatchdogServiceName(), product.AgentServiceName()} {
		st, err := svc.Query(name)
		if err != nil {
			_, _ = fmt.Fprintf(stdout, "Serviço %s: desconhecido (%v)\n", name, err)
			continue
		}
		_, _ = fmt.Fprintf(stdout, "Serviço %s: %s (pid %d) %s\n", name, st.State, st.PID, st.Exe)
	}
	hc := &http.Client{Timeout: 3 * time.Second}
	resp, err := hc.Get("http://" + *addr + "/health")
	if err != nil {
		_, _ = fmt.Fprintf(stdout, "/health do watchdog: sem resposta (%v)\n", err)
		return 1
	}
	defer func() { _ = resp.Body.Close() }()
	var rep health.Report
	if err := json.NewDecoder(resp.Body).Decode(&rep); err != nil {
		return fail(stderr, "resposta inválida do /health: %v", err)
	}
	out, _ := json.MarshalIndent(rep, "", "  ")
	_, _ = fmt.Fprintf(stdout, "/health do watchdog:\n%s\n", out)
	if rep.Status != "ok" {
		return 1
	}
	return 0
}
