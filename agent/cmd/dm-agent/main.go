// Command dm-agent: coletor de leituras de impressoras.
package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"syscall"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/agent"
	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/cli"
	"github.com/daticopy/dati-monitor/agent/internal/config"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/logx"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/secret"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

func definition(dataDir string) svc.Definition {
	args := []string{"service"}
	if dataDir != "" && dataDir != product.DataDir(runtime.GOOS) {
		args = append(args, "--data-dir", dataDir)
	}
	return svc.Definition{
		Name:        product.AgentServiceName(),
		DisplayName: product.Name + " — Coletor",
		Description: "Coleta leituras de impressoras (SNMP) e envia ao " + product.Name + ".",
		Arguments:   args,
	}
}

func main() {
	app := cli.App{Binary: "dm-agent", Description: "coletor de leituras de impressoras", Commands: map[string]cli.Command{
		"enroll":    {Summary: "cadastra o coletor com o código do portal", Run: cmdEnroll},
		"run":       {Summary: "executa o coletor no terminal (Ctrl+C encerra)", Run: cmdRun},
		"service":   {Summary: "uso interno: executado pelo gerenciador de serviços", Run: cmdService},
		"install":   {Summary: "instala o serviço (terminal como administrador)", Run: serviceControl("install")},
		"uninstall": {Summary: "remove o serviço (terminal como administrador)", Run: serviceControl("uninstall")},
		"start":     {Summary: "inicia o serviço", Run: serviceControl("start")},
		"stop":      {Summary: "para o serviço", Run: serviceControl("stop")},
		"restart":   {Summary: "reinicia o serviço", Run: serviceControl("restart")},
		"status":    {Summary: "mostra o estado do serviço e do /health", Run: cmdStatus},
	}}
	os.Exit(app.Main(os.Args[1:], os.Stdout, os.Stderr))
}

func fail(stderr io.Writer, format string, a ...any) int {
	_, _ = fmt.Fprintf(stderr, "ERRO: "+format+"\n", a...)
	return 1
}

func normalizeCode(code string) string {
	return strings.ToUpper(strings.NewReplacer(" ", "", "-", "").Replace(strings.TrimSpace(code)))
}

func cmdEnroll(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("enroll", flag.ContinueOnError)
	fs.SetOutput(stderr)
	server := fs.String("server", "", "endereço do servidor (https://...)")
	code := fs.String("code", "", "código de cadastro de 8 caracteres gerado no portal")
	dataDir := fs.String("data-dir", "", "pasta de dados (padrão do sistema)")
	insecure := fs.Bool("insecure-dev", false, "aceita http:// fora do localhost (somente desenvolvimento)")
	proxy := fs.String("proxy", "", "proxy HTTP manual (ex.: http://proxy:3128)")
	healthAddr := fs.String("health-addr", "", "endereço do /health (padrão "+health.DefaultAddr+")")
	force := fs.Bool("force", false, "substitui um cadastro existente")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *server == "" || *code == "" {
		return fail(stderr, "informe --server e --code")
	}
	if err := osinfo.CheckSupported(); err != nil {
		return fail(stderr, "%v", err)
	}
	dir := config.DataDir(*dataDir)
	if err := config.EnsureDirs(dir); err != nil {
		return fail(stderr, "%v", err)
	}
	if existing, err := config.Load(dir); err == nil && !*force {
		return fail(stderr, "este PC já está cadastrado como coletor %s; use --force para substituir", existing.AgentID)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	opts := api.Options{ServerURL: *server, InsecureDev: *insecure, ProxyURL: *proxy}
	resp, err := api.Enroll(ctx, opts, protocol.EnrollRequest{
		Code: normalizeCode(*code), Hostname: osinfo.Hostname(), OS: osinfo.Describe(), Arch: runtime.GOARCH,
		Kind: osinfo.Kind(), Version: buildinfo.Version, LocalIPs: osinfo.LocalIPv4(), HostMAC: osinfo.HostMAC(),
	})
	if err != nil {
		return fail(stderr, "cadastro recusado: %v", err)
	}
	raw, err := base64.StdEncoding.DecodeString(resp.Secret)
	if err != nil || len(raw) != secret.Size {
		return fail(stderr, "o servidor devolveu uma credencial inválida")
	}
	if err := secret.Save(dir, raw); err != nil {
		return fail(stderr, "guardar credencial: %v", err)
	}
	local := &config.Local{
		ServerURL: strings.TrimRight(*server, "/"), AgentID: resp.AgentID, InsecureDev: *insecure, ProxyURL: *proxy,
		HealthAddr: *healthAddr, EnrolledAt: time.Now().UTC(),
	}
	if err := config.Save(dir, local); err != nil {
		return fail(stderr, "salvar configuração: %v", err)
	}
	_, _ = fmt.Fprintf(stdout, "Coletor cadastrado: %s\nPasta de dados: %s\n", resp.AgentID, dir)
	return 0
}

func runAgent(ctx context.Context, dir string, console bool) error {
	if err := config.EnsureDirs(dir); err != nil {
		return err
	}
	lg, closer := logx.New(logx.Options{Dir: dir + "/logs", Name: "agent", Level: os.Getenv("DM_LOG_LEVEL"), Console: console})
	defer func() { _ = closer.Close() }()
	a, err := agent.New(dir, lg)
	if err != nil {
		lg.Error("coletor não pôde iniciar", "erro", err)
		return err
	}
	err = a.Run(ctx)
	if err != nil {
		lg.Error("coletor terminou com erro", "erro", err)
	}
	return err
}

func cmdRun(args []string, _, stderr io.Writer) int {
	fs := flag.NewFlagSet("run", flag.ContinueOnError)
	fs.SetOutput(stderr)
	dataDir := fs.String("data-dir", "", "pasta de dados")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := runAgent(ctx, config.DataDir(*dataDir), true); err != nil {
		return fail(stderr, "%v", err)
	}
	return 0
}

func cmdService(args []string, _, stderr io.Writer) int {
	fs := flag.NewFlagSet("service", flag.ContinueOnError)
	fs.SetOutput(stderr)
	dataDir := fs.String("data-dir", "", "pasta de dados")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	dir := config.DataDir(*dataDir)
	runner := &svc.Runner{Run: func(ctx context.Context) error { return runAgent(ctx, dir, false) }}
	s, err := svc.New(definition(*dataDir), runner)
	if err != nil {
		return fail(stderr, "%v", err)
	}
	if err := s.Run(); err != nil {
		return fail(stderr, "%v", err)
	}
	return 0
}

func serviceControl(action string) func([]string, io.Writer, io.Writer) int {
	return func(args []string, stdout, stderr io.Writer) int {
		fs := flag.NewFlagSet(action, flag.ContinueOnError)
		fs.SetOutput(stderr)
		dataDir := fs.String("data-dir", "", "pasta de dados")
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
				return fail(stderr, "%v", err)
			}
			err = svc.Install(s, def)
		case "uninstall":
			_ = s.Stop()
			err = s.Uninstall()
		case "start":
			err = s.Start()
		case "stop":
			err = s.Stop()
		case "restart":
			err = s.Restart()
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
	dataDir := fs.String("data-dir", "", "pasta de dados")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	state, err := svc.ServiceState(product.AgentServiceName())
	if err != nil {
		state = "desconhecido: " + err.Error()
	}
	_, _ = fmt.Fprintf(stdout, "Serviço %s: %s\n", product.AgentServiceName(), state)
	addr := health.DefaultAddr
	if l, err := config.Load(config.DataDir(*dataDir)); err == nil && l.HealthAddr != "" {
		addr = l.HealthAddr
	}
	hc := &http.Client{Timeout: 3 * time.Second}
	resp, err := hc.Get("http://" + addr + "/health")
	if err != nil {
		_, _ = fmt.Fprintf(stdout, "/health: sem resposta (%v)\n", err)
		return 1
	}
	defer func() { _ = resp.Body.Close() }()
	var rep health.Report
	if err := json.NewDecoder(resp.Body).Decode(&rep); err != nil {
		return fail(stderr, "resposta inválida do /health: %v", err)
	}
	out, _ := json.MarshalIndent(rep, "", "  ")
	_, _ = fmt.Fprintf(stdout, "/health (%s):\n%s\n", resp.Status, out)
	if rep.Status != "ok" {
		return 1
	}
	return 0
}
