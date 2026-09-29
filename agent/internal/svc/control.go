package svc

import (
	"strings"
)

// Normalized service states (same words as protocol.Service*: the agent and the watchdog report them).
const (
	StateRunning      = "running"
	StateStopped      = "stopped"
	StateStarting     = "starting"
	StateStopping     = "stopping"
	StateNotInstalled = "not_installed"
	StateUnknown      = "unknown"
)

// Status is what one product service needs to know about the other: state, process and binary.
type Status struct {
	State string
	PID   int
	Exe   string // caminho do executável registrado no serviço
}

// exeFromCommandLine returns the executable of a Windows ImagePath / command line ("C:\a b\x.exe" arg…).
func exeFromCommandLine(cmd string) string {
	cmd = strings.TrimSpace(cmd)
	if strings.HasPrefix(cmd, `"`) {
		if end := strings.Index(cmd[1:], `"`); end >= 0 {
			return cmd[1 : end+1]
		}
		return strings.Trim(cmd, `"`)
	}
	lower := strings.ToLower(cmd)
	if i := strings.Index(lower, ".exe"); i >= 0 {
		return cmd[:i+4]
	}
	if i := strings.IndexByte(cmd, ' '); i >= 0 {
		return cmd[:i]
	}
	return cmd
}

// execStartPath extracts path= from systemd's ExecStart property ("{ path=/usr/bin/x ; argv[]=… }").
func execStartPath(prop string) string {
	for _, field := range strings.Split(prop, ";") {
		field = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(field), "{"))
		if v, ok := strings.CutPrefix(field, "path="); ok {
			return strings.TrimSpace(v)
		}
	}
	return ""
}
