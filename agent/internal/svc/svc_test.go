package svc

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestDefinitionConfig(t *testing.T) {
	d := Definition{Name: "DatiMonitorAgent", DisplayName: "Dati Monitor - Coletor", Description: "d", Arguments: []string{"run"}}
	c := d.configFor("linux")
	if c.Name != d.Name || c.Arguments[0] != "run" || c.Option["DelayedAutoStart"] != true || c.Option["StartType"] != "automatic" {
		t.Fatalf("%+v", c)
	}
	if len(c.Dependencies) != 2 || !strings.Contains(c.Dependencies[1], "network-online.target") {
		t.Fatalf("dependências padrão: %v", c.Dependencies)
	}
	unit, _ := c.Option["SystemdScript"].(string)
	for _, want := range []string{"Type=notify", "Restart=always", "RestartSec=5", "WatchdogSec=60"} {
		if !strings.Contains(unit, want) {
			t.Errorf("unit sem %q", want)
		}
	}
	// Windows: nenhuma dependência no SCM (as linhas do systemd viravam nomes de serviço inexistentes).
	if got := d.configFor("windows").Dependencies; len(got) != 0 {
		t.Fatalf("dependências no Windows: %v", got)
	}
	d.Dependencies = []string{"After=x"}
	if got := d.configFor("linux").Dependencies; len(got) != 1 {
		t.Fatalf("%v", got)
	}
	if got := d.configFor("windows").Dependencies; len(got) != 0 {
		t.Fatalf("dependências no Windows: %v", got)
	}
	if _, err := New(d, &Runner{}); err != nil {
		t.Fatal(err)
	}
}

func TestRunnerStartStop(t *testing.T) {
	var r Runner
	if err := r.Stop(nil); err != nil {
		t.Fatal("parar sem ter iniciado")
	}
	started := make(chan struct{})
	r.Run = func(ctx context.Context) error {
		close(started)
		<-ctx.Done()
		return ctx.Err()
	}
	if err := r.Start(nil); err != nil {
		t.Fatal(err)
	}
	<-started
	if err := r.Stop(nil); err != nil {
		t.Fatalf("cancelamento normal não é erro: %v", err)
	}

	boom := errors.New("falhou")
	r2 := Runner{Run: func(ctx context.Context) error { <-ctx.Done(); return boom }}
	if err := r2.Start(nil); err != nil {
		t.Fatal(err)
	}
	if err := r2.Stop(nil); !errors.Is(err, boom) {
		t.Fatalf("erro do serviço deve aparecer: %v", err)
	}
}
