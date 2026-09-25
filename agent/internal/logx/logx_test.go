package logx

import (
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWritesJSONLinesAtTheConfiguredLevel(t *testing.T) {
	dir := t.TempDir()
	log, closer := New(Options{Dir: dir, Name: "agent", Level: "warn"})
	log.Info("não aparece")
	log.Warn("fila local cheia", "itens", 10)
	if err := closer.Close(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, "agent.log"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 1 {
		t.Fatalf("linhas: %q", lines)
	}
	var rec map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &rec); err != nil || rec["msg"] != "fila local cheia" || rec["itens"] != float64(10) {
		t.Fatalf("%v %v", rec, err)
	}
	if ts, _ := rec["time"].(string); !strings.HasSuffix(ts, "Z") {
		t.Fatalf("horário do log deve estar em UTC: %q", ts)
	}
}

func TestParseLevel(t *testing.T) {
	for in, want := range map[string]slog.Level{
		"debug": slog.LevelDebug, "WARNING": slog.LevelWarn, "warn": slog.LevelWarn, "error": slog.LevelError,
		"info": slog.LevelInfo, "": slog.LevelInfo, "xyz": slog.LevelInfo,
	} {
		if got := parseLevel(in); got != want {
			t.Errorf("%q -> %v", in, got)
		}
	}
}
