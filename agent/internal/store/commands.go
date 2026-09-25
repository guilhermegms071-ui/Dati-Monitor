package store

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

// CommandRecord is the local bookkeeping of a remote command: its last state and whether that state
// already reached the server. It makes command execution idempotent (a repeated delivery is not run
// again) and guarantees the final result is reported even across disconnections and restarts.
type CommandRecord struct {
	ID         string
	Type       string
	State      string
	LastUpdate []byte // JSON of protocol.CommandUpdate
	Reported   bool
	CreatedAt  time.Time
	UpdatedAt  time.Time
}

// CommandRetention is how long finished command records are kept.
const CommandRetention = 7 * 24 * time.Hour

// GetCommand returns the record of a command (nil when unknown).
func (s *Store) GetCommand(ctx context.Context, id string) (*CommandRecord, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT id, type, state, last_update, reported, created_at, updated_at FROM commands WHERE id = ?`, id)
	var r CommandRecord
	var reported int
	var created, updated int64
	err := row.Scan(&r.ID, &r.Type, &r.State, &r.LastUpdate, &reported, &created, &updated)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	r.Reported = reported != 0
	r.CreatedAt, r.UpdatedAt = time.UnixMilli(created).UTC(), time.UnixMilli(updated).UTC()
	return &r, nil
}

// PutCommandState records a new state (not yet reported).
func (s *Store) PutCommandState(ctx context.Context, id, typ, state string, update []byte, now time.Time) error {
	ms := now.UnixMilli()
	_, err := s.db.ExecContext(ctx, `
INSERT INTO commands (id, type, state, last_update, reported, created_at, updated_at)
VALUES (?, ?, ?, ?, 0, ?, ?)
ON CONFLICT(id) DO UPDATE SET state = excluded.state, last_update = excluded.last_update,
    reported = 0, updated_at = excluded.updated_at`, id, typ, state, update, ms, ms)
	return err
}

// MarkCommandReported marks the given state as delivered to the server (only if it is still the
// current one: a newer state recorded meanwhile stays pending).
func (s *Store) MarkCommandReported(ctx context.Context, id, state string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE commands SET reported = 1 WHERE id = ? AND state = ?`, id, state)
	return err
}

// UnreportedCommands lists records whose current state has not reached the server.
func (s *Store) UnreportedCommands(ctx context.Context) ([]CommandRecord, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, type, state, last_update, created_at, updated_at FROM commands WHERE reported = 0 ORDER BY updated_at`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []CommandRecord
	for rows.Next() {
		var r CommandRecord
		var created, updated int64
		if err := rows.Scan(&r.ID, &r.Type, &r.State, &r.LastUpdate, &created, &updated); err != nil {
			return nil, err
		}
		r.CreatedAt, r.UpdatedAt = time.UnixMilli(created).UTC(), time.UnixMilli(updated).UTC()
		out = append(out, r)
	}
	return out, rows.Err()
}

// CommandsInState lists records currently in one of the given states.
func (s *Store) CommandsInState(ctx context.Context, states ...string) ([]CommandRecord, error) {
	var out []CommandRecord
	for _, st := range states {
		rows, err := s.db.QueryContext(ctx, `SELECT id, type, state, last_update FROM commands WHERE state = ?`, st)
		if err != nil {
			return nil, err
		}
		for rows.Next() {
			var r CommandRecord
			if err := rows.Scan(&r.ID, &r.Type, &r.State, &r.LastUpdate); err != nil {
				_ = rows.Close()
				return nil, err
			}
			out = append(out, r)
		}
		err = rows.Err()
		_ = rows.Close()
		if err != nil {
			return nil, err
		}
	}
	return out, nil
}

// PruneCommands deletes reported records older than the retention.
func (s *Store) PruneCommands(ctx context.Context, now time.Time) (int64, error) {
	res, err := s.db.ExecContext(ctx, `DELETE FROM commands WHERE reported = 1 AND updated_at < ?`,
		now.Add(-CommandRetention).UnixMilli())
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}
