//go:build !windows

package osinfo

import (
	"os"
	"runtime"
	"strings"
	"syscall"
	"time"
)

// CheckSupported always succeeds: Linux (kernel 3.2+, Raspberry Pi OS) is always supported by the Go runtime.
func CheckSupported() error { return nil }

// Describe returns the OS description sent in heartbeats (PRETTY_NAME from /etc/os-release).
func Describe() string {
	raw, err := os.ReadFile("/etc/os-release")
	if err == nil {
		for _, line := range strings.Split(string(raw), "\n") {
			if v, ok := strings.CutPrefix(line, "PRETTY_NAME="); ok {
				return strings.Trim(v, `"`) + " " + runtime.GOARCH
			}
		}
	}
	return runtime.GOOS + " " + runtime.GOARCH
}

func processCPUTime() time.Duration {
	var ru syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &ru); err != nil {
		return 0
	}
	return time.Duration(ru.Utime.Nano() + ru.Stime.Nano())
}

// KeepAwake is a Windows-only feature; on Linux it does nothing.
func KeepAwake(bool) error { return nil }
