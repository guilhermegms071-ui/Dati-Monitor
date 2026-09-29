package watchdog

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// TestMain doubles as the child process of TestProcessTarget (a stand-in "agent" that just runs).
func TestMain(m *testing.M) {
	if os.Getenv("DM_WATCHDOG_TEST_CHILD") == "1" {
		time.Sleep(2 * time.Minute)
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func TestProcessTargetStartsStopsAndMeasures(t *testing.T) {
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("DM_WATCHDOG_TEST_CHILD", "1")
	pt := &ProcessTarget{Path: exe, Args: []string{"-test.run=^$"}, LogPath: filepath.Join(t.TempDir(), "agent.log")}
	ctx := context.Background()
	if st, _ := pt.Status(ctx); st.State != svc.StateStopped {
		t.Fatalf("antes de iniciar: %+v", st)
	}
	if err := pt.Start(ctx); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = pt.Stop(ctx) })
	st, _ := pt.Status(ctx)
	if st.State != svc.StateRunning || st.PID <= 0 || st.Exe != exe {
		t.Fatalf("rodando: %+v", st)
	}
	if err := pt.Start(ctx); err != nil { // já rodando: não abre outro
		t.Fatal(err)
	}
	if again, _ := pt.Status(ctx); again.PID != st.PID {
		t.Fatalf("Start duplicou o processo: %d → %d", st.PID, again.PID)
	}
	if mem, err := osinfo.ProcessMemory(st.PID); err != nil || mem == 0 {
		t.Fatalf("memória do filho: %d %v", mem, err)
	}
	if err := pt.Stop(ctx); err != nil {
		t.Fatal(err)
	}
	if st, _ := pt.Status(ctx); st.State != svc.StateStopped {
		t.Fatalf("depois de parar: %+v", st)
	}
	if err := pt.Start(ctx); err != nil {
		t.Fatal(err)
	}
	if st, _ := pt.Status(ctx); st.State != svc.StateRunning {
		t.Fatalf("reinício: %+v", st)
	}
	if err := pt.Uninstall(ctx); err != nil {
		t.Fatal(err)
	}
}

func TestServiceTargetOfMissingService(t *testing.T) {
	st, err := ServiceTarget{Name: "DatiMonitorServicoQueNaoExiste"}.Status(context.Background())
	if err != nil || st.State != svc.StateNotInstalled {
		t.Fatalf("serviço inexistente: %+v %v", st, err)
	}
}
