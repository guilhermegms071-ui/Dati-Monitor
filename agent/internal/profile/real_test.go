package profile

import (
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

const realDir = "../../../profiles/recordings/real"

// realExpectation is the optional <name>.expected.json next to a real recording: the values printed on
// the machine's counter sheet at the moment of the walk (PROMPT 6.6/10).
type realExpectation struct {
	Profile  string           `json:"profile"`
	Serial   string           `json:"serial"`
	Counters map[string]int64 `json:"counters"`
}

// TestRealRecordings runs every walk saved from the portal (Perfis de modelos → "Salvar como gravação
// de teste") through the same engine used against the simulator.
func TestRealRecordings(t *testing.T) {
	profiles := loadProfiles(t)
	files, err := filepath.Glob(filepath.Join(realDir, "*.snmprec"))
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("%d gravações reais em %s", len(files), realDir)
	for _, path := range files {
		name := strings.TrimSuffix(filepath.Base(path), ".snmprec")
		t.Run(name, func(t *testing.T) {
			f, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = f.Close() }()
			pdus, err := snmp.ParseSnmprec(f)
			if err != nil {
				t.Fatal(err)
			}
			src := snmp.NewMemSource(pdus)
			ctx := context.Background()
			p := Select(profiles, sysObjectID(t, src), "")
			id, err := ResolveIdentity(ctx, src, p)
			if err != nil {
				t.Fatal(err)
			}
			p = Select(profiles, sysObjectID(t, src), id.Model)
			res, err := Evaluate(ctx, src, p, id.Model)
			if err != nil {
				t.Fatal(err)
			}
			if _, ok := res.Counters["total"]; !ok {
				t.Fatalf("perfil %s não resolveu o contador total (fonte %q)", p.ID, res.Source)
			}
			exp, err := loadRealExpectation(filepath.Join(realDir, name+".expected.json"))
			if err != nil {
				t.Fatal(err)
			}
			if exp == nil {
				return
			}
			if exp.Profile != "" && exp.Profile != p.ID {
				t.Errorf("perfil = %s, esperado %s", p.ID, exp.Profile)
			}
			if exp.Serial != "" && exp.Serial != id.Serial {
				t.Errorf("série = %q, esperado %q", id.Serial, exp.Serial)
			}
			for k, want := range exp.Counters {
				if got, ok := res.Counters[k]; !ok || got != want {
					t.Errorf("contador %s = %d (resolvido=%v), folha diz %d", k, got, ok, want)
				}
			}
		})
	}
}

func loadRealExpectation(path string) (*realExpectation, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return nil, nil //nolint:nilnil // sem folha de contadores: só verifica que o total resolve
	}
	if err != nil {
		return nil, err
	}
	var exp realExpectation
	if err := json.Unmarshal(data, &exp); err != nil {
		return nil, err
	}
	return &exp, nil
}
