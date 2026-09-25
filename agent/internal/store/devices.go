package store

import (
	"context"
	"database/sql"
	"errors"
	"time"
)

// Device is a printer known to this agent (found by discovery).
type Device struct {
	IP           string
	Port         int
	Serial       string
	SysObjectID  string
	Model        string
	ProfileKey   string
	CredentialID string
	Identity     string // JSON de printer.Identity
	FirstSeen    time.Time
	LastOK       time.Time
	Failures     int
	StatusHash   string
	StatusSent   time.Time
}

// UpsertDevice inserts or updates a device (keeps first_seen and the status bookkeeping).
func (s *Store) UpsertDevice(ctx context.Context, d Device) error {
	now := time.Now().UTC().UnixMilli()
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO devices(ip, port, serial, sys_object_id, model, profile_key, credential_id, identity, first_seen, last_ok)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(ip, port) DO UPDATE SET
			serial = excluded.serial, sys_object_id = excluded.sys_object_id, model = excluded.model,
			profile_key = excluded.profile_key, credential_id = excluded.credential_id,
			identity = excluded.identity, last_ok = excluded.last_ok, failures = 0`,
		d.IP, d.Port, d.Serial, d.SysObjectID, d.Model, d.ProfileKey, d.CredentialID, orEmptyJSON(d.Identity), now, now)
	return err
}

func orEmptyJSON(s string) string {
	if s == "" {
		return "{}"
	}
	return s
}

// Devices lists the known devices ordered by IP/port.
func (s *Store) Devices(ctx context.Context) ([]Device, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT ip, port, serial, sys_object_id, model, profile_key, credential_id,
		identity, first_seen, last_ok, failures, status_hash, status_sent FROM devices ORDER BY ip, port`)
	if err != nil {
		return nil, err
	}
	defer func() { _ = rows.Close() }()
	var out []Device
	for rows.Next() {
		d, err := scanDevice(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, rows.Err()
}

// DeviceAt returns the device at ip:port (nil if unknown).
func (s *Store) DeviceAt(ctx context.Context, ip string, port int) (*Device, error) {
	row := s.db.QueryRowContext(ctx, `SELECT ip, port, serial, sys_object_id, model, profile_key, credential_id,
		identity, first_seen, last_ok, failures, status_hash, status_sent FROM devices WHERE ip = ? AND port = ?`, ip, port)
	d, err := scanDevice(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil //nolint:nilnil // ausência é um resultado válido
	}
	if err != nil {
		return nil, err
	}
	return &d, nil
}

type scanner interface{ Scan(dest ...any) error }

func scanDevice(r scanner) (Device, error) {
	var d Device
	var first, last, sent int64
	err := r.Scan(&d.IP, &d.Port, &d.Serial, &d.SysObjectID, &d.Model, &d.ProfileKey, &d.CredentialID,
		&d.Identity, &first, &last, &d.Failures, &d.StatusHash, &sent)
	if err != nil {
		return Device{}, err
	}
	d.FirstSeen = time.UnixMilli(first).UTC()
	if last > 0 {
		d.LastOK = time.UnixMilli(last).UTC()
	}
	if sent > 0 {
		d.StatusSent = time.UnixMilli(sent).UTC()
	}
	return d, nil
}

// MarkOK resets the failure counter after a successful read.
func (s *Store) MarkOK(ctx context.Context, ip string, port int) error {
	_, err := s.db.ExecContext(ctx, `UPDATE devices SET failures = 0, last_ok = ? WHERE ip = ? AND port = ?`,
		time.Now().UTC().UnixMilli(), ip, port)
	return err
}

// MarkFailure increments the failure counter and returns the new value.
func (s *Store) MarkFailure(ctx context.Context, ip string, port int) (int, error) {
	if _, err := s.db.ExecContext(ctx, `UPDATE devices SET failures = failures + 1 WHERE ip = ? AND port = ?`, ip, port); err != nil {
		return 0, err
	}
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT failures FROM devices WHERE ip = ? AND port = ?`, ip, port).Scan(&n)
	return n, err
}

// SetStatusSent records the hash of the last status sent (status is only resent when it changes,
// plus one confirmation per hour — PROMPT 4.6).
func (s *Store) SetStatusSent(ctx context.Context, ip string, port int, hash string, at time.Time) error {
	_, err := s.db.ExecContext(ctx, `UPDATE devices SET status_hash = ?, status_sent = ? WHERE ip = ? AND port = ?`,
		hash, at.UTC().UnixMilli(), ip, port)
	return err
}

// CredentialFor returns the credential that worked for ip:port ("" if unknown).
func (s *Store) CredentialFor(ctx context.Context, ip string, port int) (string, error) {
	d, err := s.DeviceAt(ctx, ip, port)
	if err != nil || d == nil {
		return "", err
	}
	return d.CredentialID, nil
}

// RemoveDevice forgets a device (e.g. when its IP left the approved ranges).
func (s *Store) RemoveDevice(ctx context.Context, ip string, port int) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM devices WHERE ip = ? AND port = ?`, ip, port)
	return err
}
