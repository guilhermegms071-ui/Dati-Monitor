//go:build !windows

package osinfo

import "golang.org/x/sys/unix"

// LowerPriority runs this process with nice 10: when the machine is busy other programs come first.
func LowerPriority() error {
	return unix.Setpriority(unix.PRIO_PROCESS, 0, 10)
}
