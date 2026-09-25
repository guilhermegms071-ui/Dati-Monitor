package uploader

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/backoff"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

type fakeSender struct {
	calls   int
	fail    error
	reject  map[string]bool
	partial bool
	got     []protocol.Item
}

func (f *fakeSender) UploadReadings(_ context.Context, req protocol.ReadingsRequest) (*protocol.ReadingsResponse, error) {
	f.calls++
	if f.fail != nil {
		return nil, f.fail
	}
	f.got = append(f.got, req.Items...)
	resp := &protocol.ReadingsResponse{V: 1}
	for i, it := range req.Items {
		if f.partial && i%2 == 1 {
			continue // servidor não confirmou: deve ficar na fila
		}
		st := protocol.ResultAccepted
		if f.reject[it.Device.Serial] {
			resp.Results = append(resp.Results, protocol.ItemResult{Key: it.Key, Status: protocol.ResultRejected, Reason: "formato"})
			continue
		}
		if it.Device.Serial == "DUP" {
			st = protocol.ResultDuplicate
		}
		resp.Results = append(resp.Results, protocol.ItemResult{Key: it.Key, Status: st})
	}
	return resp, nil
}

func setup(t *testing.T, sender Sender) (*Uploader, *store.Store) {
	t.Helper()
	st, err := store.Open(filepath.Join(t.TempDir(), "agent.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	u := &Uploader{Store: st, Send: sender, AgentID: "agent-1", Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Health: health.NewRegistry()}
	return u, st
}

func enqueue(t *testing.T, st *store.Store, serial string) int64 {
	t.Helper()
	raw, _ := json.Marshal(protocol.Item{Kind: protocol.KindReading, ReadAt: time.Now(), Device: protocol.DeviceRef{Serial: serial}})
	seq, err := st.Enqueue(context.Background(), protocol.KindReading, raw, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	return seq
}

func TestFlushAcksAcceptedAndDuplicatesAndDeadLettersRejected(t *testing.T) {
	ctx := context.Background()
	s := &fakeSender{reject: map[string]bool{"BAD": true}}
	u, st := setup(t, s)
	seq := enqueue(t, st, "OK1")
	enqueue(t, st, "DUP")
	enqueue(t, st, "BAD")
	if _, err := st.Enqueue(ctx, protocol.KindReading, []byte("{quebrado"), time.Now()); err != nil {
		t.Fatal(err)
	}
	n, err := u.Flush(ctx)
	if err != nil || n != 4 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if s.got[0].Key != "agent-1:"+itoa(seq) {
		t.Fatalf("chave de idempotência: %s", s.got[0].Key)
	}
	left, _ := st.Count(ctx)
	dead, _ := st.DeadCount(ctx)
	if left != 0 || dead != 2 {
		t.Fatalf("restantes=%d dead=%d", left, dead)
	}
	if u.LastSuccess().IsZero() || u.LastError() != "" {
		t.Fatal("estado de sucesso não registrado")
	}
}

func itoa(n int64) string { return strconv.FormatInt(n, 10) }

func TestFailureKeepsItemsAndUnconfirmedAreResent(t *testing.T) {
	ctx := context.Background()
	s := &fakeSender{fail: errors.New("sem internet")}
	u, st := setup(t, s)
	for range 4 {
		enqueue(t, st, "X")
	}
	if _, err := u.Flush(ctx); err == nil {
		t.Fatal("esperava erro")
	}
	if n, _ := st.Count(ctx); n != 4 {
		t.Fatalf("itens perdidos: %d", n)
	}
	items, _ := st.Pending(ctx, 10)
	if items[0].Attempts != 1 || !strings.Contains(u.LastError(), "sem internet") {
		t.Fatalf("%+v %s", items[0], u.LastError())
	}
	s.fail, s.partial = nil, true
	if _, err := u.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	if n, _ := st.Count(ctx); n != 2 {
		t.Fatalf("só os confirmados saem da fila: %d", n)
	}
	s.partial = false
	if _, err := u.Flush(ctx); err != nil {
		t.Fatal(err)
	}
	if n, _ := st.Count(ctx); n != 0 {
		t.Fatalf("fila deveria esvaziar: %d", n)
	}
	permanent := &api.Error{Status: 400, Code: "bad", Message: "x"}
	s.fail = permanent
	enqueue(t, st, "Y")
	if _, err := u.Flush(ctx); !errors.As(err, &permanent) {
		t.Fatal(err)
	}
}

func TestRunDrainsAndRespectsContext(t *testing.T) {
	s := &fakeSender{}
	u, st := setup(t, s)
	for range 3 {
		enqueue(t, st, "R")
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { u.Run(ctx); close(done) }()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if n, _ := st.Count(context.Background()); n == 0 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	u.Kick()
	cancel()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Run não terminou com o contexto")
	}
	if n, _ := st.Count(context.Background()); n != 0 {
		t.Fatalf("fila não esvaziou: %d", n)
	}
	if u.Health.Snapshot().Status != "ok" {
		t.Fatal("loop deveria estar saudável")
	}
}

func TestRetentionCountsDropped(t *testing.T) {
	u, st := setup(t, &fakeSender{})
	u.MaxItems = 1
	u.init()
	for range 3 {
		enqueue(t, st, "Z")
	}
	u.retention(context.Background())
	if u.Dropped() != 2 {
		t.Fatalf("descartados=%d", u.Dropped())
	}
}

func TestBackoffGrowsAndResets(t *testing.T) {
	b := backoff.New(time.Second, 8*time.Second)
	b.Jitter = 0
	var got []time.Duration
	for range 5 {
		got = append(got, b.Next())
	}
	want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 8 * time.Second}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%v", got)
		}
	}
	if b.Attempts() != 5 {
		t.Fatal("tentativas")
	}
	b.Reset()
	if b.Next() != time.Second {
		t.Fatal("reset")
	}
	j := backoff.New(10*time.Second, time.Minute)
	for range 20 {
		d := j.Next()
		if d < 8*time.Second || d > 72*time.Second {
			t.Fatalf("jitter fora de ±20%%: %v", d)
		}
	}
}
