// Package logx configures structured JSON logging to <data>/logs/<name>.log with rotation
// (10 files × 10 MB, PROMPT 4.1), optionally mirrored to stderr (console runs).
package logx

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"

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
