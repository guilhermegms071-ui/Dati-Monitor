package config

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

func TestDataDirPrecedence(t *testing.T) {
	t.Setenv(EnvDataDir, "")
	if DataDir("") != product.DataDir(runtime.GOOS) {
		t.Fatal("padrão do sistema")
	}
	t.Setenv(EnvDataDir, "D:\\dm")
	if DataDir("") != "D:\\dm" || DataDir("E:\\x") != "E:\\x" {
		t.Fatal("explícito > DM_DATA_DIR > padrão")
	}
}

func TestSaveLoadAndErrors(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "dados")
	if err := EnsureDirs(dir); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "logs")); err != nil {
		t.Fatal("pasta de logs não criada")
	}
	if _, err := Load(dir); !errors.Is(err, ErrNotEnrolled) {
		t.Fatalf("sem arquivo: %v", err)
	}
	l := &Local{
		ServerURL: "https://monitor.example.com", AgentID: "a1", EnrolledAt: time.Date(2026, 9, 25, 12, 0, 0, 0, time.UTC),
		Server: &protocol.AgentConfig{ConfigVersion: 7, SiteID: "s1"},
	}
	if err := Save(dir, l); err != nil {
		t.Fatal(err)
	}
	if err := Save(dir, l); err != nil { // sobrescreve de forma atômica
		t.Fatal(err)
	}
	got, err := Load(dir)
	if err != nil || got.AgentID != "a1" || got.Server.ConfigVersion != 7 || !got.EnrolledAt.Equal(l.EnrolledAt) {
		t.Fatalf("%+v %v", got, err)
	}
	leftovers, _ := filepath.Glob(filepath.Join(dir, FileName+".tmp-*"))
	if len(leftovers) != 0 {
		t.Fatalf("arquivos temporários sobraram: %v", leftovers)
	}
	if err := WriteAtomic(filepath.Join(dir, FileName), []byte(`{"server_url":"x"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(dir); !errors.Is(err, ErrNotEnrolled) {
		t.Fatalf("sem agent_id: %v", err)
	}
	if err := WriteAtomic(filepath.Join(dir, FileName), []byte(`{`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(dir); err == nil || errors.Is(err, ErrNotEnrolled) {
		t.Fatalf("arquivo corrompido deveria dar erro claro: %v", err)
	}
	if err := WriteAtomic(filepath.Join(dir, "nao-existe", "x.json"), []byte("{}"), 0o600); err == nil {
		t.Fatal("pasta inexistente deveria falhar")
	}
}
