// Package product derives every product-specific name (services, folders,
// titles) from the constants generated from product.json.
package product

//go:generate go run ./gen ../../../product.json product_gen.go

import "path/filepath"

// AgentServiceName is the Windows service / systemd unit name of dm-agent.
func AgentServiceName() string { return ServicePrefix + "Agent" }

// WatchdogServiceName is the Windows service / systemd unit name of dm-watchdog.
func WatchdogServiceName() string { return ServicePrefix + "Watchdog" }

// DataDir returns the agent data directory for the given GOOS.
func DataDir(goos string) string {
	if goos == "windows" {
		return `C:\ProgramData\` + ServicePrefix
	}
	return filepath.ToSlash(filepath.Join("/var/lib", Slug))
}
