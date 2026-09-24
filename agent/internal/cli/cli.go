// Package cli implements the command-line entry shared by the dm-* binaries.
// Each binary registers its subcommands; unknown commands are reported as errors.
package cli

import (
	"fmt"
	"io"
	"runtime"
	"sort"

	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/product"
)

// Command is a subcommand of a dm-* binary.
type Command struct {
	Summary string
	Run     func(args []string, stdout, stderr io.Writer) int
}

// App describes one binary.
type App struct {
	Binary      string
	Description string
	Commands    map[string]Command
}

// VersionLine is the text printed by the "version" subcommand.
func VersionLine(binary string) string {
	return fmt.Sprintf("%s %s %s (commit %s, %s/%s, %s)",
		product.Name, binary, buildinfo.Version, buildinfo.Commit,
		runtime.GOOS, runtime.GOARCH, runtime.Version())
}

// Main dispatches args (without the program name) and returns the exit code.
func (a App) Main(args []string, stdout, stderr io.Writer) int {
	cmds := map[string]Command{
		"version": {
			Summary: "mostra a versão",
			Run: func(_ []string, out, _ io.Writer) int {
				_, _ = fmt.Fprintln(out, VersionLine(a.Binary))
				return 0
			},
		},
	}
	for k, v := range a.Commands {
		cmds[k] = v
	}
	if len(args) == 0 || args[0] == "help" || args[0] == "-h" || args[0] == "--help" {
		a.usage(stdout, cmds)
		if len(args) == 0 {
			return 2
		}
		return 0
	}
	c, ok := cmds[args[0]]
	if !ok {
		_, _ = fmt.Fprintf(stderr, "%s: comando desconhecido %q\n\n", a.Binary, args[0])
		a.usage(stderr, cmds)
		return 2
	}
	return c.Run(args[1:], stdout, stderr)
}

func (a App) usage(w io.Writer, cmds map[string]Command) {
	_, _ = fmt.Fprintf(w, "%s — %s (%s)\n\nUso: %s <comando>\n\nComandos:\n", a.Binary, a.Description, product.Name, a.Binary)
	names := make([]string, 0, len(cmds))
	for n := range cmds {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		_, _ = fmt.Fprintf(w, "  %-10s %s\n", n, cmds[n].Summary)
	}
}
