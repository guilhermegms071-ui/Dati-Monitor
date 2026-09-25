// Package backoff implements exponential backoff with jitter (PROMPT 4.3: 1 s → max 60 s, forever).
package backoff

import (
	"math/rand/v2"
	"sync"
	"time"
)

// Backoff computes successive delays.
type Backoff struct {
	Min, Max time.Duration
	Jitter   float64 // fração, ex.: 0.2 = ±20%

	mu      sync.Mutex
	attempt int
}

// New returns a backoff from min to max with ±20% jitter.
func New(minDelay, maxDelay time.Duration) *Backoff {
	return &Backoff{Min: minDelay, Max: maxDelay, Jitter: 0.2}
}

// Next returns the next delay and advances.
func (b *Backoff) Next() time.Duration {
	b.mu.Lock()
	defer b.mu.Unlock()
	d := b.Min << min(b.attempt, 30) //nolint:gosec // G115: limitado a 30
	if d <= 0 || d > b.Max {
		d = b.Max
	}
	b.attempt++
	if b.Jitter > 0 {
		delta := float64(d) * b.Jitter
		d = time.Duration(float64(d) - delta + rand.Float64()*2*delta) //nolint:gosec // G404: jitter não é segurança
	}
	if d < 0 {
		d = b.Min
	}
	return d
}

// Reset goes back to the minimum delay (after a success).
func (b *Backoff) Reset() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.attempt = 0
}

// Attempts returns how many delays were handed out since the last reset.
func (b *Backoff) Attempts() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.attempt
}
