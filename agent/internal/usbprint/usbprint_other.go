//go:build !windows

package usbprint

import (
	"context"
	"errors"
	"fmt"
	"io"
	"time"
)

// List: no USB inventory outside Windows (the Linux agent reads network printers).
func List(context.Context) ([]Printer, error) { return nil, nil }

// PageCount is not available outside Windows.
func PageCount(context.Context, Printer) (int64, string, error) {
	return 0, "", errors.New("leitura USB só no Windows")
}

// SpoolerQuery needs the Windows print spooler.
func SpoolerQuery(string, time.Duration) ([]byte, error) {
	return nil, errors.New("consulta pela fila de impressão só existe no Windows")
}

// Diagnose: USB printers are only inventoried on Windows.
func Diagnose(_ context.Context, w io.Writer, _ bool) error {
	_, err := fmt.Fprintln(w, "O inventário de impressoras USB só existe no coletor do Windows.")
	return err
}
