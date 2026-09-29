package main

import "testing"

func TestVersionLineMatches(t *testing.T) {
	line := "Dati Monitor dm-agent 1.2.0 (commit abc123, windows/amd64, go1.27)"
	cases := []struct {
		component, version string
		want               bool
	}{
		{"agent", "1.2.0", true},
		{"agent", "1.2", false},       // prefixo de versão não vale
		{"agent", "1.2.0-rc1", false}, // outra versão
		{"watchdog", "1.2.0", false},  // outro programa
	}
	for _, c := range cases {
		if got := versionLineMatches(line, c.component, c.version); got != c.want {
			t.Errorf("%s %s: %v, esperado %v", c.component, c.version, got, c.want)
		}
	}
	if versionLineMatches("Dati Monitor dm-tool 1.2.0 (commit x)", "agent", "1.2.0") {
		t.Error("dm-tool com a mesma versão passou como coletor")
	}
}
