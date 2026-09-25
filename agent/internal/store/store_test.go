package store

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func open(t *testing.T) (*Store, string) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "agent.db")
	s, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, path
}

func TestOutboxIsDurableAndOrdered(t *testing.T) {
	ctx := context.Background()
	s, path := open(t)
	now := time.Now()
	var seqs []int64
	for i, kind := range []string{"reading", "supplies", "reading"} {
		seq, err := s.Enqueue(ctx, kind, []byte(`{"n":`+string(rune('0'+i))+`}`), now)
		if err != nil {
			t.Fatal(err)
		}
		seqs = append(seqs, seq)
	}
	// Reabrir o banco (simula queda do processo): nada se perde.
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	s2, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s2.Close() }()
	items, err := s2.Pending(ctx, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 3 || items[0].Seq != seqs[0] || items[2].Kind != "reading" || string(items[1].Payload) != `{"n":1}` {
		t.Fatalf("%+v", items)
	}
	if err := s2.MarkAttempt(ctx, []int64{seqs[0]}, "timeout"); err != nil {
		t.Fatal(err)
	}
	if err := s2.Ack(ctx, []int64{seqs[1]}); err != nil {
		t.Fatal(err)
	}
	if err := s2.DeadLetter(ctx, seqs[2], "rejected: formato"); err != nil {
		t.Fatal(err)
	}
	if err := s2.DeadLetter(ctx, 999, "x"); err == nil {
		t.Fatal("dead letter de item inexistente deveria falhar")
	}
	left, _ := s2.Pending(ctx, 10)
	if len(left) != 1 || left[0].Attempts != 1 {
		t.Fatalf("%+v", left)
	}
	n, _ := s2.Count(ctx)
	dead, _ := s2.DeadCount(ctx)
	if n != 1 || dead != 1 {
		t.Fatalf("count=%d dead=%d", n, dead)
	}
	if err := s2.Ack(ctx, nil); err != nil {
		t.Fatal(err)
	}
}

func TestRetentionDropsSuppliesBeforeCounters(t *testing.T) {
	ctx := context.Background()
	s, _ := open(t)
	now := time.Now()
	old := now.Add(-31 * 24 * time.Hour)
	for _, it := range []struct {
		kind string
		at   time.Time
	}{
		{"reading", old}, {"supplies", old}, {"reading", now}, {"supplies", now}, {"supplies", now},
		{"status", now}, {"reading", now},
	} {
		if _, err := s.Enqueue(ctx, it.kind, []byte("{}"), it.at); err != nil {
			t.Fatal(err)
		}
	}
	// Limite de 3 itens: somem os antigos (> 30 dias) e depois suprimentos, status... contadores por último.
	dropped, err := s.Enforce(ctx, 3, DefaultMaxAge, now)
	if err != nil {
		t.Fatal(err)
	}
	if dropped["supplies"] != 3 || dropped["reading"] != 1 || dropped["status"] != 0 {
		t.Fatalf("descartes: %v", dropped)
	}
	items, _ := s.Pending(ctx, 10)
	kinds := ""
	for _, it := range items {
		kinds += it.Kind + ","
	}
	if kinds != "reading,status,reading," {
		t.Fatalf("restaram: %s", kinds)
	}
	// Mesmo apertando mais, contadores só saem depois de todo o resto.
	dropped, _ = s.Enforce(ctx, 1, DefaultMaxAge, now)
	if dropped["status"] != 1 || dropped["reading"] != 1 {
		t.Fatalf("%v", dropped)
	}
}

func TestDevicesAndKV(t *testing.T) {
	ctx := context.Background()
	s, _ := open(t)
	if d, err := s.DeviceAt(ctx, "10.0.0.1", 161); err != nil || d != nil {
		t.Fatalf("%v %v", d, err)
	}
	dev := Device{IP: "10.0.0.1", Port: 161, Serial: "S1", SysObjectID: "1.3.6.1.4.1.1602", Model: "iR", ProfileKey: "canon", CredentialID: "c1"}
	if err := s.UpsertDevice(ctx, dev); err != nil {
		t.Fatal(err)
	}
	if n, _ := s.MarkFailure(ctx, "10.0.0.1", 161); n != 1 {
		t.Fatalf("falhas=%d", n)
	}
	if n, _ := s.MarkFailure(ctx, "10.0.0.1", 161); n != 2 {
		t.Fatalf("falhas=%d", n)
	}
	if err := s.MarkOK(ctx, "10.0.0.1", 161); err != nil {
		t.Fatal(err)
	}
	if err := s.SetStatusSent(ctx, "10.0.0.1", 161, "abc", time.Now()); err != nil {
		t.Fatal(err)
	}
	got, err := s.DeviceAt(ctx, "10.0.0.1", 161)
	if err != nil || got == nil || got.Failures != 0 || got.StatusHash != "abc" || got.Identity != "{}" || got.StatusSent.IsZero() {
		t.Fatalf("%+v %v", got, err)
	}
	if cred, _ := s.CredentialFor(ctx, "10.0.0.1", 161); cred != "c1" {
		t.Fatal(cred)
	}
	if cred, _ := s.CredentialFor(ctx, "10.9.9.9", 161); cred != "" {
		t.Fatal(cred)
	}
	all, _ := s.Devices(ctx)
	if len(all) != 1 {
		t.Fatal(all)
	}
	if err := s.RemoveDevice(ctx, "10.0.0.1", 161); err != nil {
		t.Fatal(err)
	}
	if v, _ := s.Get(ctx, "x"); v != "" {
		t.Fatal(v)
	}
	if err := s.Set(ctx, "x", "1"); err != nil {
		t.Fatal(err)
	}
	if err := s.Set(ctx, "x", "2"); err != nil {
		t.Fatal(err)
	}
	if v, _ := s.Get(ctx, "x"); v != "2" {
		t.Fatal(v)
	}
}

func TestOpenInvalidPath(t *testing.T) {
	if _, err := Open(filepath.Join(t.TempDir(), "nao", "existe", "agent.db")); err == nil {
		t.Fatal("esperava erro")
	}
}
