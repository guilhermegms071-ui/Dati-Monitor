//go:build !windows

package osinfo

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// ProcessMemory returns the resident memory (VmRSS) of another process.
func ProcessMemory(pid int) (uint64, error) {
	return statusKB(pid, "VmRSS:")
}

// PrivateMemory returns the anonymous resident memory (RssAnon: heap and stacks, without shared libraries
// and mapped files) of a process: what grows on a leak.
func PrivateMemory(pid int) (uint64, error) {
	return statusKB(pid, "RssAnon:")
}

func statusKB(pid int, field string) (uint64, error) {
	if pid <= 0 {
		return 0, fmt.Errorf("processo inválido (pid %d)", pid)
	}
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return 0, fmt.Errorf("memória do processo %d: %w", pid, err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if v, ok := strings.CutPrefix(line, field); ok {
			kb, err := strconv.ParseUint(strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(v), "kB")), 10, 64)
			if err != nil {
				return 0, fmt.Errorf("%s inválido: %q", field, v)
			}
			return kb * 1024, nil
		}
	}
	return 0, fmt.Errorf("processo %d sem %s", pid, field)
}

// OpenHandles returns how many file descriptors this process holds (soak test: a leak shows up here).
func OpenHandles() (int, error) {
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		return 0, fmt.Errorf("descritores abertos: %w", err)
	}
	return len(entries), nil
}
