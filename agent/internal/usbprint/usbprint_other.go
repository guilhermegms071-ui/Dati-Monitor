//go:build !windows

package usbprint

import (
	"context"
	"errors"
)

// List returns no printers: USB inventory is Windows-only (PROMPT 11: WMI); the Linux agent reads network printers.
func List(context.Context) ([]Printer, error) { return nil, nil }

// PageCount is not available outside Windows.
func PageCount(context.Context, Printer) (int64, string, error) {
	return 0, "", errors.New("leitura USB só no Windows")
}
