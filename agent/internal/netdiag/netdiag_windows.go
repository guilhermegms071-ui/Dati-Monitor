//go:build windows

package netdiag

import (
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

// ICMP pelo IcmpSendEcho (iphlpapi): funciona sem privilégio de administrador, ao contrário de um
// socket ICMP bruto.
var (
	iphlpapi            = windows.NewLazySystemDLL("iphlpapi.dll")
	procIcmpCreateFile  = iphlpapi.NewProc("IcmpCreateFile")
	procIcmpCloseHandle = iphlpapi.NewProc("IcmpCloseHandle")
	procIcmpSendEcho    = iphlpapi.NewProc("IcmpSendEcho")
)

type ipOptionInformation struct {
	TTL         uint8
	Tos         uint8
	Flags       uint8
	OptionsSize uint8
	OptionsData uintptr
}

type icmpEchoReply struct {
	Address       uint32
	Status        uint32
	RoundTripTime uint32
	DataSize      uint16
	Reserved      uint16
	Data          uintptr
	Options       ipOptionInformation
}

const ipSuccess = 0

func icmpEcho(ip net.IP, timeout time.Duration, _ int) (time.Duration, error) {
	h, _, err := procIcmpCreateFile.Call()
	if h == uintptr(windows.InvalidHandle) {
		return 0, fmt.Errorf("IcmpCreateFile: %w", err)
	}
	defer func() { _, _, _ = procIcmpCloseHandle.Call(h) }()
	payload := []byte("dati-monitor-ping")
	reply := make([]byte, int(unsafe.Sizeof(icmpEchoReply{}))+len(payload)+8)
	dest := binary.LittleEndian.Uint32(ip.To4()) // IPAddr em ordem de rede
	start := time.Now()
	n, _, callErr := procIcmpSendEcho.Call(
		h, uintptr(dest),
		uintptr(unsafe.Pointer(&payload[0])), uintptr(len(payload)), //nolint:gosec // G103: API Win32
		0,
		uintptr(unsafe.Pointer(&reply[0])), uintptr(len(reply)), //nolint:gosec // G103: API Win32
		uintptr(timeout.Milliseconds()), //nolint:gosec // G115: prazo curto definido pelo próprio coletor (segundos)
	)
	elapsed := time.Since(start)
	if n == 0 {
		if errors.Is(callErr, windows.Errno(11010)) { // IP_REQ_TIMED_OUT
			return 0, errors.New("sem resposta (tempo esgotado)")
		}
		return 0, fmt.Errorf("sem resposta: %w", callErr)
	}
	r := (*icmpEchoReply)(unsafe.Pointer(&reply[0])) //nolint:gosec // G103: buffer preenchido pela API
	if r.Status != ipSuccess {
		return 0, fmt.Errorf("ICMP respondeu com status %d", r.Status)
	}
	if r.RoundTripTime > 0 {
		return time.Duration(r.RoundTripTime) * time.Millisecond, nil
	}
	return elapsed, nil
}

func diskUsage(path string) (total, free uint64, err error) {
	p, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, 0, err
	}
	var avail, tot, totFree uint64
	if err := windows.GetDiskFreeSpaceEx(p, &avail, &tot, &totFree); err != nil {
		return 0, 0, err
	}
	return tot, avail, nil
}
