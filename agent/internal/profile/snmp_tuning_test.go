package profile

import (
	"strings"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// O perfil kyocera.yaml real: a ECOSYS M3655idn (link com perda na rede real, Fase 10) recebe mais
// tentativas curtas; os demais modelos Kyocera continuam com as opções do coletor.
func TestKyoceraProfileTunesOnlyTheM3655idn(t *testing.T) {
	var kyocera *Profile
	for _, p := range loadProfiles(t) {
		if p.ID == "kyocera" {
			kyocera = p
		}
	}
	if kyocera == nil {
		t.Fatal("perfil kyocera não carregado")
	}
	base := snmp.DefaultOptions()
	got := kyocera.TuneSNMP(base, "ECOSYS M3655idn")
	if got.Retries != 6 || got.Timeout != time.Second || got.RequestInterval != 0 || got.MaxRepetitions != base.MaxRepetitions {
		t.Fatalf("M3655idn: %+v", got)
	}
	if other := kyocera.TuneSNMP(base, "ECOSYS M3550idn"); other != base {
		t.Fatalf("M3550idn não deveria mudar: %+v", other)
	}
	if none := kyocera.TuneSNMP(base, ""); none != base {
		t.Fatalf("sem modelo (descoberta) não deveria mudar: %+v", none)
	}
}

func TestTuneSNMPProfileThenFirstMatchingModel(t *testing.T) {
	p, err := FromYAML([]byte(`
id: t
version: 1
counters:
  total: { oid: "1.3.6.1.2.1.43.10.2.1.4.1.1" }
snmp:
  retries: 3
  request_interval_ms: 50
  models:
    - { model_regex: "(?i)^x1", max_repetitions: 5, timeout_ms: 2500 }
    - { model_regex: "(?i)x", retries: 9 }
`))
	if err != nil {
		t.Fatal(err)
	}
	base := snmp.DefaultOptions()
	all := p.TuneSNMP(base, "Y9")
	if all.Retries != 3 || all.RequestInterval != 50*time.Millisecond || all.Timeout != base.Timeout {
		t.Fatalf("valores do perfil: %+v", all)
	}
	x1 := p.TuneSNMP(base, "X1 Pro")
	if x1.Retries != 3 || x1.MaxRepetitions != 5 || x1.Timeout != 2500*time.Millisecond {
		t.Fatalf("primeiro modelo que casa sobrepõe o perfil: %+v", x1)
	}
	if x2 := p.TuneSNMP(base, "X2"); x2.Retries != 9 {
		t.Fatalf("segundo modelo: %+v", x2)
	}
	var nilProfile *Profile
	if got := nilProfile.TuneSNMP(base, "X1"); got != base {
		t.Fatalf("sem perfil: %+v", got)
	}
}

func TestSNMPBlockIsValidated(t *testing.T) {
	for name, block := range map[string]string{
		"tentativas demais":    "snmp: { retries: 99 }",
		"timeout curto demais": "snmp: { timeout_ms: 10 }",
		"campo desconhecido":   "snmp: { pausa: 3 }",
		"modelo sem regex":     "snmp: { models: [ { retries: 2 } ] }",
		"regex inválida":       `snmp: { models: [ { model_regex: "(", retries: 2 } ] }`,
	} {
		doc := "id: t\nversion: 1\ncounters:\n  total: { oid: \"1.3.6.1.2.1.43.10.2.1.4.1.1\" }\n" + block + "\n"
		if _, err := FromYAML([]byte(doc)); err == nil {
			t.Errorf("%s: deveria ser recusado", name)
		} else if strings.TrimSpace(err.Error()) == "" {
			t.Errorf("%s: erro sem mensagem", name)
		}
	}
}
