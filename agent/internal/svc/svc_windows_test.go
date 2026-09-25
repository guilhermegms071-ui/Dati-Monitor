//go:build windows

package svc

import (
	"errors"
	"testing"
)

// Sem privilégio (terminal comum) ou sem o serviço instalado, o erro é claro e em português.
func TestRestartServiceClearErrors(t *testing.T) {
	err := RestartService("DatiMonitorServicoQueNaoExiste")
	if !errors.Is(err, ErrNoPermission) && !errors.Is(err, ErrNotInstalled) {
		t.Fatalf("erro inesperado: %v", err)
	}
}
