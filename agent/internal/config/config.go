// Package config handles the agent's local files in the data directory: config.json (server URL,
// agent id, last configuration received from the server) — written atomically.
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
)

// EnvDataDir overrides the data directory (tests, portable runs).
const EnvDataDir = "DM_DATA_DIR"

// FileName of the local configuration.
const FileName = "config.json"

// ErrNotEnrolled means the agent has no agent_id yet (run "dm-agent enroll").
var ErrNotEnrolled = errors.New("coletor não cadastrado: rode \"dm-agent enroll --server URL --code CODIGO\"")

// Local is the persisted local configuration.
type Local struct {
	ServerURL   string                `json:"server_url"`
	AgentID     string                `json:"agent_id"`
	InsecureDev bool                  `json:"insecure_dev,omitempty"`
	ProxyURL    string                `json:"proxy_url,omitempty"`
	HealthAddr  string                `json:"health_addr,omitempty"`
	WSURL       string                `json:"ws_url,omitempty"` // canal WebSocket informado no cadastro
	EnrolledAt  time.Time             `json:"enrolled_at"`
	Server      *protocol.AgentConfig `json:"server,omitempty"`
}

// DataDir resolves the data directory: explicit value > DM_DATA_DIR > OS default
// (C:\ProgramData\DatiMonitor or /var/lib/dati-monitor).
func DataDir(explicit string) string {
	if explicit != "" {
		return explicit
	}
	if env := os.Getenv(EnvDataDir); env != "" {
		return env
	}
	return product.DataDir(runtime.GOOS)
}

// EnsureDirs creates the data and log directories.
func EnsureDirs(dir string) error {
	for _, d := range []string{dir, filepath.Join(dir, "logs")} {
		if err := os.MkdirAll(d, 0o750); err != nil {
			return fmt.Errorf("criar pasta %s: %w", d, err)
		}
	}
	return nil
}

// Load reads config.json from dir.
func Load(dir string) (*Local, error) {
	raw, err := os.ReadFile(filepath.Join(dir, FileName)) //nolint:gosec // G304: pasta de dados do próprio agente
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrNotEnrolled
	}
	if err != nil {
		return nil, fmt.Errorf("ler configuração local: %w", err)
	}
	var l Local
	if err := json.Unmarshal(raw, &l); err != nil {
		return nil, fmt.Errorf("configuração local corrompida (%s): %w", filepath.Join(dir, FileName), err)
	}
	if l.AgentID == "" || l.ServerURL == "" {
		return nil, ErrNotEnrolled
	}
	return &l, nil
}

// Save writes config.json atomically (temp file + rename), so a crash never leaves it half-written.
func Save(dir string, l *Local) error {
	raw, err := json.MarshalIndent(l, "", "  ")
	if err != nil {
		return err
	}
	return WriteAtomic(filepath.Join(dir, FileName), raw, 0o600)
}

// WriteAtomic writes data to path via a temporary file in the same directory and a rename.
func WriteAtomic(path string, data []byte, perm os.FileMode) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	cleanup := func() { _ = os.Remove(name) }
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		cleanup()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		cleanup()
		return err
	}
	if err := tmp.Close(); err != nil {
		cleanup()
		return err
	}
	if err := os.Chmod(name, perm); err != nil {
		cleanup()
		return err
	}
	if err := os.Rename(name, path); err != nil {
		cleanup()
		return err
	}
	return nil
}
