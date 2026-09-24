// Package buildinfo holds values injected at link time by scripts/build-agent.ps1.
package buildinfo

// Version is the semantic version of the binary, set with
// -ldflags "-X github.com/daticopy/dati-monitor/agent/internal/buildinfo.Version=x.y.z".
var Version = "0.0.0-dev"

// Commit is the git commit the binary was built from.
var Commit = "unknown"
