//go:build windows

package api

import (
	"encoding/binary"

	"golang.org/x/sys/windows/registry"
)

// systemProxy reads the machine-wide WinHTTP proxy ("netsh winhttp set proxy"), which is what a
// service running as LocalSystem should use.
func systemProxy() *sysProxy {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE,
		`SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Connections`, registry.QUERY_VALUE)
	if err != nil {
		return nil
	}
	defer func() { _ = k.Close() }()
	blob, _, err := k.GetBinaryValue("WinHttpSettings")
	if err != nil {
		return nil
	}
	server, bypassList, ok := parseWinHTTPSettings(blob)
	if !ok {
		return nil
	}
	return parseProxyList(server, bypassList)
}

// parseWinHTTPSettings decodes the WinHttpSettings blob:
// DWORD version, DWORD counter, DWORD flags (bit 1 = proxy), DWORD len + proxy, DWORD len + bypass.
func parseWinHTTPSettings(b []byte) (server, bypassList string, ok bool) {
	if len(b) < 16 {
		return "", "", false
	}
	flags := binary.LittleEndian.Uint32(b[8:12])
	if flags&0x2 == 0 {
		return "", "", false
	}
	n := int(binary.LittleEndian.Uint32(b[12:16]))
	if n < 0 || 16+n > len(b) {
		return "", "", false
	}
	server = string(b[16 : 16+n])
	rest := b[16+n:]
	if len(rest) >= 4 {
		m := int(binary.LittleEndian.Uint32(rest[:4]))
		if m >= 0 && 4+m <= len(rest) {
			bypassList = string(rest[4 : 4+m])
		}
	}
	return server, bypassList, server != ""
}
