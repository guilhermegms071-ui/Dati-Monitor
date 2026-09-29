//go:build windows

package osinfo

import (
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

// processMemoryCounters is PROCESS_MEMORY_COUNTERS (psapi.h).
type processMemoryCounters struct {
	cb                         uint32
	PageFaultCount             uint32
	PeakWorkingSetSize         uintptr
	WorkingSetSize             uintptr
	QuotaPeakPagedPoolUsage    uintptr
	QuotaPagedPoolUsage        uintptr
	QuotaPeakNonPagedPoolUsage uintptr
	QuotaNonPagedPoolUsage     uintptr
	PagefileUsage              uintptr
	PeakPagefileUsage          uintptr
}

var procGetProcessMemoryInfo = windows.NewLazySystemDLL("kernel32.dll").NewProc("K32GetProcessMemoryInfo")

// ProcessMemory returns the working set (memory in use, as the Task Manager shows) of another process.
func ProcessMemory(pid int) (uint64, error) {
	if pid <= 0 {
		return 0, fmt.Errorf("processo inválido (pid %d)", pid)
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.PROCESS_VM_READ, false, uint32(pid)) //nolint:gosec // G115: pid positivo
	if err != nil {
		return 0, fmt.Errorf("abrir processo %d: %w", pid, err)
	}
	defer func() { _ = windows.CloseHandle(h) }()
	var c processMemoryCounters
	c.cb = uint32(unsafe.Sizeof(c))                                                                        //nolint:gosec // G103: tamanho da struct da API do Windows
	r, _, callErr := procGetProcessMemoryInfo.Call(uintptr(h), uintptr(unsafe.Pointer(&c)), uintptr(c.cb)) //nolint:gosec // G103: chamada à API psapi
	if r == 0 {
		return 0, fmt.Errorf("memória do processo %d: %w", pid, callErr)
	}
	return uint64(c.WorkingSetSize), nil
}
