package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunGeneratesConstants(t *testing.T) {
	dir := t.TempDir()
	in := filepath.Join(dir, "p.json")
	out := filepath.Join(dir, "p_gen.go")
	if err := os.WriteFile(in, []byte(`{"name":"X Mon","slug":"x-mon","service_prefix":"XMon"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := run(in, out); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(out) //nolint:gosec // G304: test-controlled temp path
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range []string{`Name = "X Mon"`, `Slug = "x-mon"`, `ServicePrefix = "XMon"`} {
		if !strings.Contains(string(b), s) {
			t.Errorf("saída sem %s:\n%s", s, b)
		}
	}
}

func TestRunRejectsInvalid(t *testing.T) {
	dir := t.TempDir()
	for name, content := range map[string]string{
		"missing.json": `{"name":"X"}`,
		"unknown.json": `{"name":"X","slug":"x","service_prefix":"X","extra":1}`,
		"broken.json":  `{`,
	} {
		in := filepath.Join(dir, name)
		if err := os.WriteFile(in, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := run(in, filepath.Join(dir, "o.go")); err == nil {
			t.Errorf("%s: esperava erro", name)
		}
	}
	if err := run(filepath.Join(dir, "nope.json"), filepath.Join(dir, "o.go")); err == nil {
		t.Error("arquivo inexistente: esperava erro")
	}
}
