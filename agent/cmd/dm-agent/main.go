// Command dm-agent: coletor de leituras de impressoras.
package main

import (
	"os"

	"github.com/daticopy/dati-monitor/agent/internal/cli"
)

func main() {
	app := cli.App{Binary: "dm-agent", Description: "coletor de leituras de impressoras"}
	os.Exit(app.Main(os.Args[1:], os.Stdout, os.Stderr))
}
