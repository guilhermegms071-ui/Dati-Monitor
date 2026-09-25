package logx

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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

func TestZipRecentPicksNewestLogs(t *testing.T) {
	dir := t.TempDir()
	now := time.Now()
	write := func(name, body string, mod time.Time) {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(p, mod, mod); err != nil {
			t.Fatal(err)
		}
	}
	write("agent.log", "atual\n", now)
	write("agent-2026-09-24T10-00-00.000.log", "rotacionado\n", now.Add(-2*time.Hour))
	write("agent-2026-09-01T10-00-00.000.log", "antigo\n", now.Add(-30*24*time.Hour))
	write("outra-coisa.txt", "x", now)
	var buf bytes.Buffer
	n, err := ZipRecent(dir, now.Add(-24*time.Hour), 1<<20, &buf)
	if err != nil || n != 2 {
		t.Fatalf("%d %v", n, err)
	}
	zr, err := zip.NewReader(bytes.NewReader(buf.Bytes()), int64(buf.Len()))
	if err != nil {
		t.Fatal(err)
	}
	if len(zr.File) != 2 || zr.File[0].Name != "agent.log" {
		t.Fatalf("arquivos: %v", zr.File)
	}
	rc, _ := zr.File[0].Open()
	body, _ := io.ReadAll(rc)
	_ = rc.Close()
	if string(body) != "atual\n" {
		t.Fatalf("%q", body)
	}
	if _, err := ZipRecent(filepath.Join(dir, "nao-existe"), now, 1, &buf); err == nil {
		t.Fatal("pasta inexistente")
	}
}
