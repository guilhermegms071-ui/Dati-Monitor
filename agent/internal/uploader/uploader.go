// Package uploader sends the outbox to POST /api/agent/readings in batches of up to 500 items
// (gzip) with an idempotency key per item; only confirmed keys are removed (PROMPT 4.3/4.4).
package uploader

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/api"
	"github.com/daticopy/dati-monitor/agent/internal/backoff"
	"github.com/daticopy/dati-monitor/agent/internal/health"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

// Tunables.
const (
	BatchSize      = 500
	Idle           = 5 * time.Second
	RetentionEvery = time.Minute
	HealthLoop     = "uploader"
)

// Sender uploads a batch (api.Client in production).
type Sender interface {
	UploadReadings(ctx context.Context, req protocol.ReadingsRequest) (*protocol.ReadingsResponse, error)
}

// Uploader drains the outbox.
type Uploader struct {
	Store    *store.Store
	Send     Sender
	AgentID  string
	Log      *slog.Logger
	Health   *health.Registry
	MaxItems int
	MaxAge   time.Duration

	kick     chan struct{}
	once     sync.Once
	dropped  atomic.Int64
	lastOK   atomic.Int64
	lastErr  atomic.Value
	backoff  *backoff.Backoff
	lastRetn time.Time
}

func (u *Uploader) init() {
	u.once.Do(func() {
		u.kick = make(chan struct{}, 1)
		u.backoff = backoff.New(time.Second, 60*time.Second)
		if u.MaxItems <= 0 {
			u.MaxItems = store.DefaultMaxItems
		}
		if u.MaxAge <= 0 {
			u.MaxAge = store.DefaultMaxAge
		}
		u.lastErr.Store("")
	})
}

// Kick asks for an immediate send (called after every enqueue).
func (u *Uploader) Kick() {
	u.init()
	select {
	case u.kick <- struct{}{}:
	default:
	}
}

// Dropped returns how many items retention discarded since start.
func (u *Uploader) Dropped() int { return int(u.dropped.Load()) }

// LastSuccess returns the time of the last successful upload.
func (u *Uploader) LastSuccess() time.Time {
	if ms := u.lastOK.Load(); ms > 0 {
		return time.UnixMilli(ms).UTC()
	}
	return time.Time{}
}

// LastError returns the last upload error ("" if the last attempt succeeded).
func (u *Uploader) LastError() string {
	u.init()
	s, _ := u.lastErr.Load().(string)
	return s
}

// Run loops until ctx ends.
func (u *Uploader) Run(ctx context.Context) {
	u.init()
	u.Health.Register(HealthLoop, Idle)
	for {
		u.Health.Beat(HealthLoop)
		u.retention(ctx)
		n, err := u.Flush(ctx)
		wait := Idle
		switch {
		case err != nil:
			wait = u.backoff.Next()
			if wait > Idle {
				// Espera longa em pedaços, para o /health continuar batendo.
				u.sleep(ctx, wait)
				continue
			}
		case n == BatchSize:
			wait = 0 // ainda há fila: continua já
		}
		if wait == 0 {
			if ctx.Err() != nil {
				return
			}
			continue
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(wait):
		case <-u.kick:
		}
	}
}

func (u *Uploader) sleep(ctx context.Context, d time.Duration) {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		u.Health.Beat(HealthLoop)
		step := min(Idle, time.Until(deadline))
		select {
		case <-ctx.Done():
			return
		case <-time.After(step):
		}
	}
}

func (u *Uploader) retention(ctx context.Context) {
	if time.Since(u.lastRetn) < RetentionEvery {
		return
	}
	u.lastRetn = time.Now()
	dropped, err := u.Store.Enforce(ctx, u.MaxItems, u.MaxAge, time.Now())
	if err != nil {
		u.Log.Error("aplicar retenção da fila local", "erro", err)
		return
	}
	total := 0
	for _, n := range dropped {
		total += n
	}
	if total > 0 {
		u.dropped.Add(int64(total))
		u.Log.Warn("fila local cheia ou antiga demais: itens descartados", "por_tipo", dropped)
	}
}

// Flush sends one batch. It returns how many items were in the batch.
func (u *Uploader) Flush(ctx context.Context) (int, error) {
	u.init()
	items, err := u.Store.Pending(ctx, BatchSize)
	if err != nil {
		return 0, fmt.Errorf("ler fila local: %w", err)
	}
	if len(items) == 0 {
		return 0, nil
	}
	req := protocol.ReadingsRequest{V: protocol.Version, Items: make([]protocol.Item, 0, len(items))}
	byKey := map[string]int64{}
	var seqs []int64
	for _, it := range items {
		var item protocol.Item
		if err := json.Unmarshal(it.Payload, &item); err != nil {
			u.Log.Error("item corrompido na fila local; movido para dead_letter", "seq", it.Seq, "erro", err)
			if derr := u.Store.DeadLetter(ctx, it.Seq, "payload inválido: "+err.Error()); derr != nil {
				return 0, derr
			}
			continue
		}
		item.Key = u.AgentID + ":" + strconv.FormatInt(it.Seq, 10)
		byKey[item.Key] = it.Seq
		seqs = append(seqs, it.Seq)
		req.Items = append(req.Items, item)
	}
	if len(req.Items) == 0 {
		return len(items), nil
	}
	resp, err := u.Send.UploadReadings(ctx, req)
	if err != nil {
		u.lastErr.Store(err.Error())
		if merr := u.Store.MarkAttempt(ctx, seqs, err.Error()); merr != nil {
			u.Log.Error("registrar tentativa de envio", "erro", merr)
		}
		var apiErr *api.Error
		if errors.As(err, &apiErr) && apiErr.Permanent() {
			u.Log.Error("servidor recusou o lote inteiro", "itens", len(seqs), "erro", err)
		} else {
			u.Log.Warn("envio de leituras falhou; nova tentativa com espera crescente", "itens", len(seqs), "erro", err)
		}
		return len(items), err
	}
	var ack []int64
	for _, r := range resp.Results {
		seq, ok := byKey[r.Key]
		if !ok {
			continue
		}
		switch r.Status {
		case protocol.ResultAccepted, protocol.ResultDuplicate, protocol.ResultDiscarded:
			ack = append(ack, seq)
		case protocol.ResultRejected:
			u.Log.Error("servidor rejeitou item; movido para dead_letter", "chave", r.Key, "motivo", r.Reason)
			if err := u.Store.DeadLetter(ctx, seq, r.Reason); err != nil {
				return len(items), err
			}
		default:
			u.Log.Warn("resultado desconhecido do servidor; item será reenviado", "chave", r.Key, "status", r.Status)
		}
	}
	if err := u.Store.Ack(ctx, ack); err != nil {
		return len(items), fmt.Errorf("confirmar itens enviados: %w", err)
	}
	u.backoff.Reset()
	u.lastErr.Store("")
	u.lastOK.Store(time.Now().UnixMilli())
	return len(items), nil
}
