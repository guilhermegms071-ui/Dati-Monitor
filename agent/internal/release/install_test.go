package release

import (
	"context"
	"crypto/ed25519"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeService is a "service" whose binary is a text file: the content is the version it runs.
type fakeService struct {
	exe     string
	running bool
	ops     []string
}

func (f *fakeService) Stop(context.Context) error {
	f.running = false
	f.ops = append(f.ops, "stop")
	return nil
}
func (f *fakeService) Start(context.Context) error {
	f.running = true
	f.ops = append(f.ops, "start")
	return nil
}
func (f *fakeService) Exe() (string, error) { return f.exe, nil }

func (f *fakeService) runningVersion(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(f.exe)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func newInstaller(t *testing.T, current string, healthy func(version string) error) (*Installer, *fakeService, ed25519.PrivateKey) {
	t.Helper()
	dir := t.TempDir()
	exe := filepath.Join(dir, "bin", "dm-agent.exe")
	if err := os.MkdirAll(filepath.Dir(exe), 0o750); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(exe, []byte(current), 0o600); err != nil {
		t.Fatal(err)
	}
	priv := testKey(t)
	svc := &fakeService{exe: exe, running: true}
	inst := &Installer{
		Service:   svc,
		StateDir:  filepath.Join(dir, "updates", "agent"),
		PublicKey: priv.Public().(ed25519.PublicKey),
		Wait:      time.Second,
		Log:       slog.New(slog.NewTextHandler(io.Discard, nil)),
		Healthy: func(_ context.Context, version string, _ time.Time) error {
			if !svc.running {
				return errors.New("parado")
			}
			if got := svc.runningVersion(t); got != version {
				return errors.New("rodando " + got)
			}
			return healthy(version)
		},
	}
	return inst, svc, priv
}

func withDownload(inst *Installer, data []byte) {
	inst.Download = func(_ context.Context, path string, limit int64) ([]byte, error) {
		if !strings.HasPrefix(path, "/api/agent/releases/") || int64(len(data)) > limit {
			return nil, errors.New("download inesperado: " + path)
		}
		return data, nil
	}
}

func TestUpdateReplacesKeepsPreviousAndRollbackSwaps(t *testing.T) {
	inst, svc, priv := newInstaller(t, "1.0.0", func(string) error { return nil })
	data := []byte("1.1.0")
	withDownload(inst, data)
	p := params(priv, "1.1.0", data)
	p.URL = "/api/agent/releases/x/file"
	res, err := inst.Update(context.Background(), p, "1.0.0")
	if err != nil {
		t.Fatal(err)
	}
	if res.From != "1.0.0" || res.To != "1.1.0" || res.RolledBack {
		t.Fatalf("resultado: %+v", res)
	}
	if svc.runningVersion(t) != "1.1.0" || !svc.running || inst.PreviousVersion() != "1.0.0" {
		t.Fatalf("depois do update: exe=%s running=%v previous=%s", svc.runningVersion(t), svc.running, inst.PreviousVersion())
	}
	if strings.Join(svc.ops, ",") != "stop,start" {
		t.Fatalf("operações: %v", svc.ops)
	}

	res, err = inst.Rollback(context.Background(), "1.1.0")
	if err != nil {
		t.Fatal(err)
	}
	if res.To != "1.0.0" || svc.runningVersion(t) != "1.0.0" || inst.PreviousVersion() != "1.1.0" {
		t.Fatalf("rollback: %+v exe=%s previous=%s", res, svc.runningVersion(t), inst.PreviousVersion())
	}
	// Um segundo rollback volta para a versão que foi desfeita.
	if _, err := inst.Rollback(context.Background(), "1.0.0"); err != nil || svc.runningVersion(t) != "1.1.0" {
		t.Fatalf("segundo rollback: %v exe=%s", err, svc.runningVersion(t))
	}
}

func TestUnhealthyVersionIsRolledBackAutomatically(t *testing.T) {
	inst, svc, priv := newInstaller(t, "1.0.0", func(version string) error {
		if version == "1.1.0" {
			return errors.New("sem heartbeat aceito pelo servidor")
		}
		return nil
	})
	data := []byte("1.1.0")
	withDownload(inst, data)
	p := params(priv, "1.1.0", data)
	p.URL = "/api/agent/releases/x/file"
	res, err := inst.Update(context.Background(), p, "1.0.0")
	if err == nil || !strings.Contains(err.Error(), "rollback para 1.0.0 feito") {
		t.Fatalf("erro: %v", err)
	}
	if !res.RolledBack || res.To != "1.0.0" {
		t.Fatalf("resultado: %+v", res)
	}
	if svc.runningVersion(t) != "1.0.0" || !svc.running {
		t.Fatalf("depois do rollback: exe=%s running=%v", svc.runningVersion(t), svc.running)
	}
}

func TestInvalidSignatureTouchesNothing(t *testing.T) {
	inst, svc, priv := newInstaller(t, "1.0.0", func(string) error { return nil })
	withDownload(inst, []byte("1.1.0-adulterado"))
	p := params(priv, "1.1.0", []byte("1.1.0"))
	p.URL = "/api/agent/releases/x/file"
	p.SizeBytes = 0
	_, err := inst.Update(context.Background(), p, "1.0.0")
	if !errors.Is(err, ErrDigest) {
		t.Fatalf("erro: %v", err)
	}
	if len(svc.ops) != 0 || svc.runningVersion(t) != "1.0.0" {
		t.Fatalf("o serviço foi mexido: %v", svc.ops)
	}
	if _, err := inst.Rollback(context.Background(), "1.0.0"); err == nil {
		t.Fatal("rollback sem versão anterior aceito")
	}
}
