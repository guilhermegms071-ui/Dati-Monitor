// Command dm-watchdog: vigia do coletor, atualização e rollback.
package main

import (
	"os"

	"github.com/daticopy/dati-monitor/agent/internal/cli"
)

func main() {
	app := cli.App{Binary: "dm-watchdog", Description: "vigia do coletor, atualização e rollback"}
	os.Exit(app.Main(os.Args[1:], os.Stdout, os.Stderr))
}
