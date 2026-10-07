//go:build windows

package usbprint

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	winspool             = windows.NewLazySystemDLL("winspool.drv")
	procOpenPrinterW     = winspool.NewProc("OpenPrinterW")
	procClosePrinter     = winspool.NewProc("ClosePrinter")
	procStartDocPrinterW = winspool.NewProc("StartDocPrinterW")
	procEndDocPrinter    = winspool.NewProc("EndDocPrinter")
	procWritePrinter     = winspool.NewProc("WritePrinter")
	procReadPrinter      = winspool.NewProc("ReadPrinter")
)

// docInfo1 is DOC_INFO_1W.
type docInfo1 struct {
	docName    *uint16
	outputFile *uint16
	datatype   *uint16
}

// SpoolerQuery sends the PJL request as a RAW job to the printer queue and reads the answer back through
// the port monitor (bidirectional USB), for printers whose driver holds the USB device. The spooler may
// block: run it in its own process (dm-agent usb-pjl), never inside the service.
func SpoolerQuery(name string, wait time.Duration) ([]byte, error) {
	n16, err := windows.UTF16PtrFromString(name)
	if err != nil {
		return nil, err
	}
	var h windows.Handle
	if r, _, e := procOpenPrinterW.Call(uintptr(unsafe.Pointer(n16)), uintptr(unsafe.Pointer(&h)), 0); r == 0 {
		return nil, fmt.Errorf("abrir a fila %q: %w", name, e)
	}
	defer func() { _, _, _ = procClosePrinter.Call(uintptr(h)) }()
	doc, _ := windows.UTF16PtrFromString("Dati Monitor - leitura do contador")
	raw, _ := windows.UTF16PtrFromString("RAW")
	di := docInfo1{docName: doc, datatype: raw}
	if r, _, e := procStartDocPrinterW.Call(uintptr(h), 1, uintptr(unsafe.Pointer(&di))); r == 0 {
		return nil, fmt.Errorf("iniciar o trabalho RAW: %w", e)
	}
	ended := false
	end := func() {
		if !ended {
			ended = true
			_, _, _ = procEndDocPrinter.Call(uintptr(h))
		}
	}
	defer end()
	data := pjlRequest("INFO ID", "INFO PAGECOUNT")
	var written uint32
	if r, _, e := procWritePrinter.Call(uintptr(h), uintptr(unsafe.Pointer(&data[0])), uintptr(len(data)),
		uintptr(unsafe.Pointer(&written))); r == 0 {
		return nil, fmt.Errorf("enviar o PJL pela fila: %w", e)
	}
	// A resposta volta pelo monitor de porta enquanto o trabalho está aberto; depois de fechar o trabalho,
	// o identificador não serve mais para ler.
	var buf bytes.Buffer
	deadline := time.Now().Add(wait)
	var lastErr error
	chunk := make([]byte, 4096)
	for time.Now().Before(deadline) {
		var got uint32
		r, _, e := procReadPrinter.Call(uintptr(h), uintptr(unsafe.Pointer(&chunk[0])), uintptr(len(chunk)),
			uintptr(unsafe.Pointer(&got)))
		if got > 0 {
			buf.Write(chunk[:got])
			if pageCountRe.Match(buf.Bytes()) && bytes.Count(buf.Bytes(), []byte("\f")) >= 2 {
				break
			}
			continue
		}
		if r == 0 && lastErr == nil {
			lastErr = e // o primeiro erro diz o motivo; os seguintes repetem
		}
		time.Sleep(200 * time.Millisecond)
	}
	end()
	if buf.Len() == 0 && lastErr != nil {
		return nil, fmt.Errorf("ler a resposta pela fila: %w", lastErr)
	}
	return buf.Bytes(), nil
}

// spoolerPageCount runs SpoolerQuery in a child process (dm-agent usb-pjl) so a stuck spooler call is killed
// with the process, and parses the answer.
func spoolerPageCount(ctx context.Context, p Printer) (int64, string, error) {
	exe, err := os.Executable()
	if err != nil {
		return 0, "", err
	}
	ctx, cancel := context.WithTimeout(ctx, PJLTimeout+10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, exe, "usb-pjl", "--printer", p.Name, "--wait", PJLTimeout.String())
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	out, err := cmd.Output()
	if ctx.Err() != nil {
		return 0, "", errors.New("pela fila de impressão: sem resposta no prazo")
	}
	if err != nil {
		var stderr string
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			stderr = strings.TrimSpace(string(ee.Stderr))
		}
		return 0, "", fmt.Errorf("pela fila de impressão: %s", firstNonEmpty(stderr, err.Error()))
	}
	count, perr := ParsePageCount(out)
	if perr != nil {
		if len(bytes.TrimSpace(out)) == 0 {
			return 0, "", errors.New("pela fila de impressão: a impressora não respondeu")
		}
		return 0, "", fmt.Errorf("pela fila de impressão: %w", perr)
	}
	return count, ParseID(out), nil
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

// Diagnose prints, for a technician, every USB printer queue of this PC: whether it is connected, how the
// collector reaches it and what the printer answers (straight on the USB interface and, with trySpooler,
// through the queue — which may print a page on printers that do not understand PJL).
func Diagnose(ctx context.Context, w io.Writer, trySpooler bool) error {
	list, err := listAll(ctx)
	if err != nil {
		return err
	}
	if len(list) == 0 {
		_, _ = fmt.Fprintln(w, "Nenhuma impressora em porta USB neste PC.")
		return nil
	}
	for _, p := range list {
		_, _ = fmt.Fprintf(w, "\n== %s ==\n", p.Name)
		_, _ = fmt.Fprintf(w, "Driver: %s\nPorta: %s\nAparelho USB: %s\n", p.Driver, p.Port, firstNonEmpty(p.Parent, "(não identificado)"))
		_, _ = fmt.Fprintf(w, "Ligada agora: %v (interface USB: %v, aparelho presente: %v)\n", p.Present, p.Path != "", p.DevicePresent)
		_, _ = fmt.Fprintf(w, "Caminho: %s\n", firstNonEmpty(p.InterfacePath(), "(nenhum)"))
		_, _ = fmt.Fprintf(w, "Driver indica PJL: %v\n", SpoolerSafe(p.Driver))
		if !p.Present {
			continue
		}
		if n, model, err := directPageCount(ctx, p); err != nil {
			_, _ = fmt.Fprintf(w, "USB direta: FALHOU: %v\n", err)
		} else {
			_, _ = fmt.Fprintf(w, "USB direta: OK, contador %d (%s)\n", n, model)
		}
		if !trySpooler {
			continue
		}
		resp, err := SpoolerQuery(p.Name, PJLTimeout)
		switch {
		case err != nil:
			_, _ = fmt.Fprintf(w, "Fila de impressão: FALHOU: %v\n", err)
		case len(resp) == 0:
			_, _ = fmt.Fprintln(w, "Fila de impressão: a impressora não respondeu")
		default:
			_, _ = fmt.Fprintf(w, "Fila de impressão: resposta %q\n", truncate(resp, 400))
			if n, perr := ParsePageCount(resp); perr == nil {
				_, _ = fmt.Fprintf(w, "Fila de impressão: OK, contador %d\n", n)
			}
		}
	}
	return nil
}
