//go:build windows

package secret

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// cryptprotectLocalMachine: any account on this machine may decrypt (the service runs as
// LocalSystem while enrollment may run under the installing admin), but the blob is useless on
// another computer. The file itself is only readable by SYSTEM/Administrators (installer ACL).
const (
	cryptprotectUIForbidden  = 0x1
	cryptprotectLocalMachine = 0x4
)

var entropy = []byte("dati-monitor-agent-credential-v1")

func blob(b []byte) *windows.DataBlob {
	if len(b) == 0 {
		return &windows.DataBlob{}
	}
	return &windows.DataBlob{Size: uint32(len(b)), Data: &b[0]} //nolint:gosec // G115: tamanho pequeno
}

// take copies the DPAPI output buffer and frees it. unsafe é inevitável: a API devolve um ponteiro
// alocado pelo Windows (LocalAlloc) com o tamanho em out.Size.
func take(out *windows.DataBlob) []byte {
	defer func() { _, _ = windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data))) }() //nolint:gosec // G103: ver acima
	return append([]byte(nil), unsafe.Slice(out.Data, out.Size)...)                       //nolint:gosec // G103: ver acima
}

func protect(data []byte) ([]byte, error) {
	var out windows.DataBlob
	if err := windows.CryptProtectData(blob(data), nil, blob(entropy), 0, nil,
		cryptprotectUIForbidden|cryptprotectLocalMachine, &out); err != nil {
		return nil, err
	}
	return take(&out), nil
}

func unprotect(data []byte) ([]byte, error) {
	var out windows.DataBlob
	if err := windows.CryptUnprotectData(blob(data), nil, blob(entropy), 0, nil, cryptprotectUIForbidden, &out); err != nil {
		return nil, err
	}
	return take(&out), nil
}
