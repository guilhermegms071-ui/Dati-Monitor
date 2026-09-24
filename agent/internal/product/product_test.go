package product

import (
	"encoding/json"
	"os"
	"testing"
)

func TestGeneratedConstantsMatchProductJSON(t *testing.T) {
	raw, err := os.ReadFile("../../../product.json")
	if err != nil {
		t.Fatal(err)
	}
	var p struct {
		Name          string `json:"name"`
		Slug          string `json:"slug"`
		ServicePrefix string `json:"service_prefix"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		t.Fatal(err)
	}
	if p.Name != Name || p.Slug != Slug || p.ServicePrefix != ServicePrefix {
		t.Fatalf("product_gen.go desatualizado (rode go generate): json=%+v", p)
	}
}

func TestDerivedNames(t *testing.T) {
	cases := map[string]string{
		AgentServiceName():    ServicePrefix + "Agent",
		WatchdogServiceName(): ServicePrefix + "Watchdog",
		DataDir("windows"):    `C:\ProgramData\` + ServicePrefix,
		DataDir("linux"):      "/var/lib/" + Slug,
	}
	for got, want := range cases {
		if got != want {
			t.Errorf("got %q want %q", got, want)
		}
	}
	if AgentServiceName() != "DatiMonitorAgent" || WatchdogServiceName() != "DatiMonitorWatchdog" {
		t.Errorf("nomes de serviço exigidos pelo PROMPT (4.1/5.1) divergem: %s %s", AgentServiceName(), WatchdogServiceName())
	}
}
