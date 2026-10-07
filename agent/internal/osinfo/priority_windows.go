//go:build windows

package osinfo

import "golang.org/x/sys/windows"

// LowerPriority runs this process "below normal": when the PC is busy the user's programs come first; the
// collector only waits a little (readings are not affected).
func LowerPriority() error {
	return windows.SetPriorityClass(windows.CurrentProcess(), windows.BELOW_NORMAL_PRIORITY_CLASS)
}
