//go:build windows

package osinfo

import (
	"fmt"
	"runtime"
	"sync"
	"time"

	"golang.org/x/sys/windows"
)

// VersionInfo is the real Windows version.
type VersionInfo struct {
	Major, Minor, Build uint32
	Server              bool
}

// CurrentVersion reads the real Windows version. RtlGetVersion is not affected by manifests, but it IS
// affected by compatibility shims (e.g. __COMPAT_LAYER=Win7RTM or "compatibility mode" on the exe), which
// would make a Windows 10 refuse to enroll; RtlGetNtVersionNumbers returns the kernel numbers unshimmed.
func CurrentVersion() VersionInfo {
	major, minor, build := windows.RtlGetNtVersionNumbers()
	v := windows.RtlGetVersion()
	return VersionInfo{Major: major, Minor: minor, Build: build & 0xffff, Server: v.ProductType != 1}
}

// Name returns a friendly name, e.g. "Windows 7" or "Windows Server 2012 R2".
func (v VersionInfo) Name() string {
	type key struct{ major, minor uint32 }
	desktop := map[key]string{{6, 0}: "Windows Vista", {6, 1}: "Windows 7", {6, 2}: "Windows 8", {6, 3}: "Windows 8.1"}
	server := map[key]string{{6, 0}: "Windows Server 2008", {6, 1}: "Windows Server 2008 R2", {6, 2}: "Windows Server 2012", {6, 3}: "Windows Server 2012 R2"}
	k := key{v.Major, v.Minor}
	if v.Server {
		if n, ok := server[k]; ok {
			return n
		}
		if v.Major >= 10 {
			return fmt.Sprintf("Windows Server (build %d)", v.Build)
		}
	} else {
		if n, ok := desktop[k]; ok {
			return n
		}
		if v.Major >= 10 {
			if v.Build >= 22000 {
				return fmt.Sprintf("Windows 11 (build %d)", v.Build)
			}
			return fmt.Sprintf("Windows 10 (build %d)", v.Build)
		}
	}
	return fmt.Sprintf("Windows %d.%d (build %d)", v.Major, v.Minor, v.Build)
}

// Supported reports whether this Windows is 10/11 or Server 2016+ (NT 10.0).
func (v VersionInfo) Supported() bool { return v.Major >= 10 }

// CheckSupported refuses Windows 7/8/8.1/2008/2012 with a clear Portuguese message (PROMPT 4.1).
func CheckSupported() error {
	return checkVersion(CurrentVersion())
}

func checkVersion(v VersionInfo) error {
	if v.Supported() {
		return nil
	}
	return fmt.Errorf("este computador usa %s. Instale o coletor em um PC com Windows 10 ou mais novo na mesma rede", v.Name())
}

// Describe returns the OS description sent in heartbeats.
func Describe() string {
	return CurrentVersion().Name() + " " + runtime.GOARCH
}

func processCPUTime() time.Duration {
	var creation, exit, kernel, user windows.Filetime
	if err := windows.GetProcessTimes(windows.CurrentProcess(), &creation, &exit, &kernel, &user); err != nil {
		return 0
	}
	toDur := func(ft windows.Filetime) time.Duration {
		return time.Duration(uint64(ft.HighDateTime)<<32|uint64(ft.LowDateTime)) * 100 //nolint:gosec // G115: intervalos de 100 ns
	}
	return toDur(kernel) + toDur(user)
}

var (
	kernel32                    = windows.NewLazySystemDLL("kernel32.dll")
	procSetThreadExecutionState = kernel32.NewProc("SetThreadExecutionState")
	awakeMu                     sync.Mutex
	awakeStop                   chan struct{}
)

const (
	esContinuous     = 0x80000000
	esSystemRequired = 0x00000001
)

// KeepAwake prevents (true) or allows again (false) Windows sleep while the service runs
// (PROMPT 4.1, optional). The execution state belongs to a thread, so a dedicated OS thread holds it.
func KeepAwake(enable bool) error {
	awakeMu.Lock()
	defer awakeMu.Unlock()
	if !enable {
		if awakeStop != nil {
			close(awakeStop)
			awakeStop = nil
		}
		return nil
	}
	if awakeStop != nil {
		return nil
	}
	if err := procSetThreadExecutionState.Find(); err != nil {
		return err
	}
	stop := make(chan struct{})
	ready := make(chan error, 1)
	go func() {
		runtime.LockOSThread()
		defer runtime.UnlockOSThread()
		r, _, err := procSetThreadExecutionState.Call(esContinuous | esSystemRequired)
		if r == 0 {
			ready <- fmt.Errorf("SetThreadExecutionState: %w", err)
			return
		}
		ready <- nil
		<-stop
		_, _, _ = procSetThreadExecutionState.Call(esContinuous)
	}()
	if err := <-ready; err != nil {
		return err
	}
	awakeStop = stop
	return nil
}
