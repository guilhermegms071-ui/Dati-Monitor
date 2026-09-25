// Package store is the agent's local SQLite database (agent.db): the outbox (every reading is
// written here BEFORE any send attempt — PROMPT 4.4), a dead-letter table, the known devices and a
// small key/value area. It uses modernc.org/sqlite (no CGO).
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	_ "modernc.org/sqlite" // driver "sqlite"
)

// Retention limits (PROMPT 4.4).
const (
	DefaultMaxItems = 200_000
	DefaultMaxAge   = 30 * 24 * time.Hour
)

// DropOrder is the order in which kinds are discarded when limits are hit: supplies first, counters
// ("reading") last — never counters before supplies.
var DropOrder = []string{"supplies", "status", "event", "reading"}

// Store wraps the SQLite database.
type Store struct {
	db *sql.DB
}

const schema = `
CREATE TABLE IF NOT EXISTS outbox (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL,
    created_at INTEGER NOT NULL,
    payload    BLOB    NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_outbox_kind_seq ON outbox(kind, seq);
CREATE TABLE IF NOT EXISTS dead_letter (
    seq        INTEGER PRIMARY KEY,
    kind       TEXT    NOT NULL,
    created_at INTEGER NOT NULL,
    payload    BLOB    NOT NULL,
    reason     TEXT    NOT NULL,
    dead_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    ip            TEXT    NOT NULL,
    port          INTEGER NOT NULL,
    serial        TEXT    NOT NULL DEFAULT '',
    sys_object_id TEXT    NOT NULL DEFAULT '',
    model         TEXT    NOT NULL DEFAULT '',
    profile_key   TEXT    NOT NULL DEFAULT '',
    credential_id TEXT    NOT NULL DEFAULT '',
    identity      TEXT    NOT NULL DEFAULT '{}',
    first_seen    INTEGER NOT NULL,
    last_ok       INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,
    status_hash   TEXT    NOT NULL DEFAULT '',
    status_sent   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ip, port)
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
`

// Open opens (creating if needed) the database with WAL and synchronous=FULL, so an enqueued item
// survives a crash or power loss.
func Open(path string) (*Store, error) {
	dsn := "file:" + path + "?_pragma=journal_mode(WAL)&_pragma=synchronous(FULL)&_pragma=busy_timeout(10000)&_pragma=foreign_keys(ON)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, fmt.Errorf("abrir %s: %w", path, err)
	}
	db.SetMaxOpenConns(1) // SQLite: um escritor; evita "database is locked"
	if _, err := db.Exec(schema); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("criar tabelas em %s: %w", path, err)
	}
	return &Store{db: db}, nil
}

// Close closes the database.
func (s *Store) Close() error { return s.db.Close() }

// OutboxItem is a queued payload.
type OutboxItem struct {
	Seq       int64
	Kind      string
	CreatedAt time.Time
	Payload   []byte
	Attempts  int
}

// Enqueue durably stores a payload and returns its sequence number.
func (s *Store) Enqueue(ctx context.Context, kind string, payload []byte, at time.Time) (int64, error) {
	res, err := s.db.ExecContext(ctx, `INSERT INTO outbox(kind, created_at, payload) VALUES (?, ?, ?)`,
		kind, at.UTC().UnixMilli(), payload)
	if err != nil {
		return 0, fmt.Errorf("gravar na fila local: %w", err)
	}
	return res.LastInsertId()
}

// Pending returns up to limit items in send order.
func (s *Store) Pending(ctx context.Context, limit int) ([]OutboxItem, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT seq, kind, created_at, payload, attempts FROM outbox ORDER BY seq LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []OutboxItem
	for rows.Next() {
		var it OutboxItem
		var ms int64
		if err := rows.Scan(&it.Seq, &it.Kind, &ms, &it.Payload, &it.Attempts); err != nil {
			return nil, err
		}
		it.CreatedAt = time.UnixMilli(ms).UTC()
		out = append(out, it)
	}
	return out, rows.Err()
}

// Ack removes items confirmed by the server.
func (s *Store) Ack(ctx context.Context, seqs []int64) error {
	if len(seqs) == 0 {
		return nil
	}
	return s.inTx(ctx, func(tx *sql.Tx) error {
		for _, seq := range seqs {
			if _, err := tx.ExecContext(ctx, `DELETE FROM outbox WHERE seq = ?`, seq); err != nil {
				return err
			}
		}
		return nil
	})
}

// MarkAttempt records a failed send attempt for the items.
func (s *Store) MarkAttempt(ctx context.Context, seqs []int64, reason string) error {
	return s.inTx(ctx, func(tx *sql.Tx) error {
		for _, seq := range seqs {
			if _, err := tx.ExecContext(ctx,
				`UPDATE outbox SET attempts = attempts + 1, last_error = ? WHERE seq = ?`, reason, seq); err != nil {
				return err
			}
		}
		return nil
	})
}

// DeadLetter moves an item the server permanently rejected out of the outbox (never silently lost:
// it stays in dead_letter, is logged and counted in diagnostics).
func (s *Store) DeadLetter(ctx context.Context, seq int64, reason string) error {
	return s.inTx(ctx, func(tx *sql.Tx) error {
		res, err := tx.ExecContext(ctx, `INSERT INTO dead_letter(seq, kind, created_at, payload, reason, dead_at)
			SELECT seq, kind, created_at, payload, ?, ? FROM outbox WHERE seq = ?`, reason, time.Now().UTC().UnixMilli(), seq)
		if err != nil {
			return err
		}
		if n, _ := res.RowsAffected(); n == 0 {
			return fmt.Errorf("item %d não está na fila", seq)
		}
		_, err = tx.ExecContext(ctx, `DELETE FROM outbox WHERE seq = ?`, seq)
		return err
	})
}

// Count returns the number of items waiting to be sent.
func (s *Store) Count(ctx context.Context) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM outbox`).Scan(&n)
	return n, err
}

// DeadCount returns the number of dead-lettered items.
func (s *Store) DeadCount(ctx context.Context) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT count(*) FROM dead_letter`).Scan(&n)
	return n, err
}

// Enforce applies the retention limits and returns how many items of each kind were discarded.
// Old items (> maxAge) go first, in DropOrder; then, if still above maxItems, the oldest items of
// each kind in DropOrder (supplies first, counters last).
func (s *Store) Enforce(ctx context.Context, maxItems int, maxAge time.Duration, now time.Time) (map[string]int, error) {
	dropped := map[string]int{}
	cutoff := now.Add(-maxAge).UTC().UnixMilli()
	err := s.inTx(ctx, func(tx *sql.Tx) error {
		for _, kind := range DropOrder {
			res, err := tx.ExecContext(ctx, `DELETE FROM outbox WHERE kind = ? AND created_at < ?`, kind, cutoff)
			if err != nil {
				return err
			}
			if n, _ := res.RowsAffected(); n > 0 {
				dropped[kind] += int(n)
			}
		}
		var count int
		if err := tx.QueryRowContext(ctx, `SELECT count(*) FROM outbox`).Scan(&count); err != nil {
			return err
		}
		excess := count - maxItems
		for _, kind := range DropOrder {
			if excess <= 0 {
				break
			}
			res, err := tx.ExecContext(ctx,
				`DELETE FROM outbox WHERE seq IN (SELECT seq FROM outbox WHERE kind = ? ORDER BY seq LIMIT ?)`, kind, excess)
			if err != nil {
				return err
			}
			n, _ := res.RowsAffected()
			dropped[kind] += int(n)
			excess -= int(n)
		}
		return nil
	})
	return dropped, err
}

// Get reads a kv value ("" if absent).
func (s *Store) Get(ctx context.Context, key string) (string, error) {
	var v string
	err := s.db.QueryRowContext(ctx, `SELECT value FROM kv WHERE key = ?`, key).Scan(&v)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	return v, err
}

// Set writes a kv value.
func (s *Store) Set(ctx context.Context, key, value string) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value`, key, value)
	return err
}

func (s *Store) inTx(ctx context.Context, fn func(*sql.Tx) error) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if err := fn(tx); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}
