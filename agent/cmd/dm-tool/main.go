// Command dm-tool: ferramentas de suporte do coletor.
package main

import (
	"os"

	"github.com/daticopy/dati-monitor/agent/internal/cli"
)

func main() {
	app := cli.App{Binary: "dm-tool", Description: "ferramentas de suporte do coletor"}
	os.Exit(app.Main(os.Args[1:], os.Stdout, os.Stderr))
}
