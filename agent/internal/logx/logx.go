// Package logx configures structured JSON logging to <data>/logs/<name>.log with rotation
// (10 files × 10 MB, PROMPT 4.1), optionally mirrored to stderr (console runs).
package logx

import (
	"archive/zip"
	"fmt"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"gopkg.in/natefinch/lumberjack.v2"
)

// Rotation limits.
const (
	MaxSizeMB  = 10
	MaxBackups = 9 // + o arquivo atual = 10 arquivos
)

// Options for New.
type Options struct {
	Dir     string // pasta de logs
	Name    string // ex.: "agent" -> agent.log
	Level   string // debug | info | warn | error
	Console bool   // também escreve no stderr
}

// New returns a JSON logger and the closer of the rotating file.
func New(o Options) (*slog.Logger, io.Closer) {
	lj := &lumberjack.Logger{
		Filename:   filepath.Join(o.Dir, o.Name+".log"),
		MaxSize:    MaxSizeMB,
		MaxBackups: MaxBackups,
		LocalTime:  false,
		Compress:   false,
	}
	var w io.Writer = lj
	if o.Console {
		w = io.MultiWriter(lj, os.Stderr)
	}
	h := slog.NewJSONHandler(w, &slog.HandlerOptions{Level: parseLevel(o.Level), ReplaceAttr: utcTime})
	return slog.New(h), lj
}

// utcTime writes every log timestamp in UTC (the project rule for anything stored), so agent logs
// line up with server logs regardless of the PC's time zone.
func utcTime(groups []string, a slog.Attr) slog.Attr {
	if len(groups) == 0 && a.Key == slog.TimeKey && a.Value.Kind() == slog.KindTime {
		a.Value = slog.TimeValue(a.Value.Time().UTC())
	}
	return a
}

func parseLevel(s string) slog.Level {
	switch strings.ToLower(s) {
	case "debug":
		return slog.LevelDebug
	case "warn", "warning":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

// ZipRecent writes to w a zip with the log files of dir modified in the last period (current file
// and rotated ones), newest first, stopping before maxBytes of compressed output. Returns how many
// files went in.
func ZipRecent(dir string, since time.Time, maxBytes int64, w io.Writer) (int, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0, fmt.Errorf("ler pasta de logs %s: %w", dir, err)
	}
	type file struct {
		name string
		mod  time.Time
	}
	var files []file
	for _, e := range entries {
		if e.IsDir() || !strings.Contains(e.Name(), ".log") {
			continue
		}
		info, err := e.Info()
		if err != nil || info.ModTime().Before(since) {
			continue
		}
		files = append(files, file{e.Name(), info.ModTime()})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mod.After(files[j].mod) })
	cw := &countingWriter{w: w}
	zw := zip.NewWriter(cw)
	n := 0
	for _, f := range files {
		if cw.n >= maxBytes {
			break
		}
		if err := addFile(zw, filepath.Join(dir, f.name), f.name, f.mod); err != nil {
			return n, err
		}
		n++
	}
	if err := zw.Close(); err != nil {
		return n, err
	}
	return n, nil
}

func addFile(zw *zip.Writer, path, name string, mod time.Time) error {
	src, err := os.Open(path) //nolint:gosec // G304: arquivos da própria pasta de logs
	if err != nil {
		return err
	}
	defer func() { _ = src.Close() }()
	dst, err := zw.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Deflate, Modified: mod})
	if err != nil {
		return err
	}
	_, err = io.Copy(dst, src)
	return err
}

type countingWriter struct {
	w io.Writer
	n int64
}

func (c *countingWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += int64(n)
	return n, err
}
