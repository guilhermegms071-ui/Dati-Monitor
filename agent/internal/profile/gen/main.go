// Command gen copies profiles/profile.schema.json next to the profile package so it can be embedded
// (go:embed cannot reach files outside the module). A test fails if the copy gets out of date.
package main

import (
	"fmt"
	"os"
	"path/filepath"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "uso: gen <origem.json> <destino.json>")
		os.Exit(2)
	}
	raw, err := os.ReadFile(filepath.Clean(os.Args[1])) //nolint:gosec // G703: caminho vem da diretiva go:generate
	if err != nil {
		fmt.Fprintln(os.Stderr, "gen:", err)
		os.Exit(1)
	}
	if err := os.WriteFile(filepath.Clean(os.Args[2]), raw, 0o600); err != nil { //nolint:gosec // G703: idem
		fmt.Fprintln(os.Stderr, "gen:", err)
		os.Exit(1)
	}
}
