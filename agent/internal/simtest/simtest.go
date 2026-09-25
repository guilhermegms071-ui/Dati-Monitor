//go:build integration

// Package simtest starts the real snmpsim (from the repository venv) with the recordings in
// profiles/recordings/sim, for integration tests (go test -tags integration).
package simtest

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// RepoRoot finds the repository root (the folder that contains product.json).
func RepoRoot(t testing.TB) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "product.json")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("product.json não encontrado acima de " + dir)
		}
		dir = parent
	}
}

func venvBin(t testing.TB, name string) string {
	t.Helper()
	root := RepoRoot(t)
	var p string
	if runtime.GOOS == "windows" {
		p = filepath.Join(root, ".venv", "Scripts", name+".exe")
	} else {
		p = filepath.Join(root, ".venv", "bin", name)
	}
	if _, err := os.Stat(p); err != nil {
		t.Fatalf("%s não encontrado (%v): crie o venv com backend/requirements-dev.lock", p, err)
	}
	return p
}

// FreeUDPPort returns a free UDP port on 127.0.0.1.
func FreeUDPPort(t testing.TB) int {
	t.Helper()
	c, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = c.Close() }()
	return c.LocalAddr().(*net.UDPAddr).Port
}

// Recording returns the folder of a simulated printer (e.g. "03-konica-cor").
func Recording(t testing.TB, name string) string {
	t.Helper()
	return filepath.Join(RepoRoot(t), "profiles", "recordings", "sim", name)
}

func startProcess(t testing.TB, bin string, args ...string) *exec.Cmd {
	t.Helper()
	logPath := filepath.Join(t.TempDir(), filepath.Base(bin)+".log")
	logf, err := os.Create(logPath) //nolint:gosec // G304: caminho temporário do teste
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(bin, args...)   //nolint:gosec // G204: binário do venv do projeto
	cmd.Stdout, cmd.Stderr = logf, logf // arquivo, nunca PIPE não lido (trava no Windows)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		_, _ = cmd.Process.Wait()
		_ = logf.Close()
		if t.Failed() {
			if b, err := os.ReadFile(logPath); err == nil { //nolint:gosec // G304: idem
				t.Logf("log de %s:\n%s", filepath.Base(bin), b)
			}
		}
	})
	return cmd
}

// V3 is the SNMPv3 user configured by StartV3 (credencial fictícia do simulador, não é segredo).
var V3 = snmp.Credential{ //nolint:gosec // G101: ver acima
	ID: "v3", Version: "v3", V3Username: "dati", V3AuthProto: "SHA256", V3AuthPass: "chave-auth-123",
	V3PrivProto: "AES256", V3PrivPass: "chave-priv-123", V3Context: "public",
}

// Public is the v2c credential of the simulator.
var Public = snmp.Credential{ID: "public", Version: "v2c", Community: "public"}

// Start runs snmpsim for one recording and returns its UDP port.
func Start(t testing.TB, name string) int {
	t.Helper()
	return start(t, name, false)
}

// StartV3 runs snmpsim with the SNMPv3 user V3 (SHA-256 + AES-256 Blumenthal).
func StartV3(t testing.TB, name string) int {
	t.Helper()
	return start(t, name, true)
}

func start(t testing.TB, name string, v3 bool) int {
	t.Helper()
	port := FreeUDPPort(t)
	startDir(t, Recording(t, name), v3, port)
	return port
}

// StartDir runs snmpsim for any folder containing public.snmprec, on the given port.
func StartDir(t testing.TB, dir string, port int) *exec.Cmd {
	t.Helper()
	return startDir(t, dir, false, port)
}

func startDir(t testing.TB, dir string, v3 bool, port int) *exec.Cmd {
	t.Helper()
	var args []string
	cred := Public
	if v3 {
		// Cada motor SNMP do snmpsim começa em --v3-engine-id e leva seus próprios dados e endpoint.
		args = append(args, "--v3-engine-id=auto", "--v3-user="+V3.V3Username,
			"--v3-auth-key="+V3.V3AuthPass, "--v3-auth-proto=SHA256",
			"--v3-priv-key="+V3.V3PrivPass, "--v3-priv-proto=AES256BLMT")
		cred = V3
	}
	args = append(args,
		"--data-dir="+dir,
		"--cache-dir="+filepath.Join(t.TempDir(), "cache"),
		fmt.Sprintf("--agent-udpv4-endpoint=127.0.0.1:%d", port),
	)
	cmd := startProcess(t, venvBin(t, "snmpsim-command-responder"), args...)
	WaitReady(t, port, cred)
	return cmd
}

// StartSleepy runs snmpsim behind the sleepy proxy (economia de energia) and returns the proxy port.
func StartSleepy(t testing.TB, name string, idle, wake time.Duration) int {
	t.Helper()
	inner := Start(t, name)
	port := FreeUDPPort(t)
	script := filepath.Join(RepoRoot(t), "scripts", "sleepy_udp_proxy.py")
	startProcess(t, venvBin(t, "python"), script,
		fmt.Sprintf("--listen=127.0.0.1:%d", port), fmt.Sprintf("--target=127.0.0.1:%d", inner),
		fmt.Sprintf("--idle=%.3f", idle.Seconds()), fmt.Sprintf("--wake=%.3f", wake.Seconds()))
	// Espera o proxy abrir a porta (sem mandar SNMP: isso "acordaria" a impressora).
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		c, err := net.ListenPacket("udp", fmt.Sprintf("127.0.0.1:%d", port))
		if err != nil { // porta em uso = proxy escutando
			time.Sleep(idle + 200*time.Millisecond) // garante que está "dormindo"
			return port
		}
		_ = c.Close()
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("proxy sonolento não subiu")
	return 0
}

// WaitReady polls sysObjectID until the simulator answers (max 60 s).
func WaitReady(t testing.TB, port int, cred snmp.Credential) {
	t.Helper()
	deadline := time.Now().Add(60 * time.Second)
	opts := snmp.Options{Timeout: 500 * time.Millisecond, Retries: 0}
	for time.Now().Before(deadline) {
		c, err := snmp.Dial("127.0.0.1", port, cred, opts)
		if err == nil {
			v, err := c.Get(context.Background(), []string{"1.3.6.1.2.1.1.2.0"})
			_ = c.Close()
			if err == nil && len(v) == 1 && v[0].Exists() {
				return
			}
		}
		time.Sleep(300 * time.Millisecond)
	}
	t.Fatalf("snmpsim na porta %d não respondeu em 60 s", port)
}
