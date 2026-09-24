package cli

import (
	"bytes"
	"io"
	"strings"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
)

func testApp() App {
	return App{Binary: "dm-test", Description: "teste", Commands: map[string]Command{
		"echo": {Summary: "ecoa", Run: func(args []string, out, _ io.Writer) int {
			_, _ = io.WriteString(out, strings.Join(args, " "))
			return 0
		}},
	}}
}

func TestVersion(t *testing.T) {
	var out, errb bytes.Buffer
	if code := testApp().Main([]string{"version"}, &out, &errb); code != 0 {
		t.Fatalf("code %d", code)
	}
	if !strings.Contains(out.String(), "Dati Monitor dm-test "+buildinfo.Version) {
		t.Fatalf("saída inesperada: %q", out.String())
	}
}

func TestDispatchAndErrors(t *testing.T) {
	var out, errb bytes.Buffer
	a := testApp()
	if code := a.Main([]string{"echo", "a", "b"}, &out, &errb); code != 0 || out.String() != "a b" {
		t.Fatalf("echo: code=%d out=%q", code, out.String())
	}
	out.Reset()
	if code := a.Main([]string{"xyz"}, &out, &errb); code != 2 || !strings.Contains(errb.String(), `comando desconhecido "xyz"`) {
		t.Fatalf("desconhecido: code=%d err=%q", code, errb.String())
	}
	out.Reset()
	if code := a.Main(nil, &out, &errb); code != 2 || !strings.Contains(out.String(), "Comandos:") {
		t.Fatalf("sem args: code=%d out=%q", code, out.String())
	}
	out.Reset()
	if code := a.Main([]string{"--help"}, &out, &errb); code != 0 || !strings.Contains(out.String(), "echo") {
		t.Fatalf("help: code=%d out=%q", code, out.String())
	}
}
