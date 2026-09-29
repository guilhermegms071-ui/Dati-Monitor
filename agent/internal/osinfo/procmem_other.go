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
	if pid <= 0 {
		return 0, fmt.Errorf("processo inválido (pid %d)", pid)
	}
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid))
	if err != nil {
		return 0, fmt.Errorf("memória do processo %d: %w", pid, err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if v, ok := strings.CutPrefix(line, "VmRSS:"); ok {
			kb, err := strconv.ParseUint(strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(v), "kB")), 10, 64)
			if err != nil {
				return 0, fmt.Errorf("VmRSS inválido: %q", v)
			}
			return kb * 1024, nil
		}
	}
	return 0, fmt.Errorf("processo %d sem VmRSS", pid)
}
