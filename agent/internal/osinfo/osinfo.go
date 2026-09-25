// Package osinfo describes the host (OS, IPs, MAC), measures the agent process and implements the
// Windows version gate (PROMPT 4.1: Windows 10/11 and Server 2016+ only).
package osinfo

import (
	"net"
	"os"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

// Hostname returns the machine name ("" on error).
func Hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return ""
	}
	return h
}

// LocalIPv4 returns the IPv4 addresses of up, non-loopback interfaces, sorted.
func LocalIPv4() []string {
	var out []string
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagUp == 0 || ifc.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, err := ifc.Addrs()
		if err != nil {
			continue
		}
		for _, a := range addrs {
			if ipn, ok := a.(*net.IPNet); ok {
				if v4 := ipn.IP.To4(); v4 != nil {
					out = append(out, v4.String())
				}
			}
		}
	}
	sort.Strings(out)
	return out
}

// PrivateSubnets24 returns the /24 networks of the private IPv4 addresses given (PROMPT 4.5: the
// agent suggests these when the site has no approved range).
func PrivateSubnets24(ips []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range ips {
		ip := net.ParseIP(s).To4()
		if ip == nil || !ip.IsPrivate() {
			continue
		}
		cidr := net.IPv4(ip[0], ip[1], ip[2], 0).String() + "/24"
		if !seen[cidr] {
			seen[cidr] = true
			out = append(out, cidr)
		}
	}
	sort.Strings(out)
	return out
}

// HostMAC returns the MAC of the first up, non-loopback interface with an IPv4 address.
func HostMAC() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagUp == 0 || ifc.Flags&net.FlagLoopback != 0 || len(ifc.HardwareAddr) != 6 {
			continue
		}
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			if ipn, ok := a.(*net.IPNet); ok && ipn.IP.To4() != nil {
				return strings.ToUpper(ifc.HardwareAddr.String())
			}
		}
	}
	return ""
}

// Kind returns "windows" or "linux" (the agents.kind column).
func Kind() string {
	if runtime.GOOS == "windows" {
		return "windows"
	}
	return "linux"
}

// ProcessMeter computes the CPU usage of this process between calls.
type ProcessMeter struct {
	mu       sync.Mutex
	lastCPU  time.Duration
	lastWall time.Time
}

// Sample returns CPU percent (of one core, divided by the number of CPUs) since the previous call
// and the memory obtained from the OS by the Go runtime.
func (m *ProcessMeter) Sample() (cpuPercent float64, memBytes uint64) {
	var ms runtime.MemStats
	runtime.ReadMemStats(&ms)
	cpu := processCPUTime()
	now := time.Now()
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.lastWall.IsZero() {
		wall := now.Sub(m.lastWall)
		if wall > 0 {
			cpuPercent = float64(cpu-m.lastCPU) / float64(wall) * 100 / float64(runtime.NumCPU())
		}
	}
	m.lastCPU, m.lastWall = cpu, now
	if cpuPercent < 0 {
		cpuPercent = 0
	}
	return cpuPercent, ms.Sys
}
