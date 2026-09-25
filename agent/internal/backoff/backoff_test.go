package backoff

import (
	"testing"
	"time"
)

func TestGrowsToMaxAndResets(t *testing.T) {
	b := &Backoff{Min: time.Second, Max: time.Minute}
	want := []time.Duration{1, 2, 4, 8, 16, 32, 60, 60}
	for i, w := range want {
		if d := b.Next(); d != w*time.Second {
			t.Fatalf("passo %d: %v, esperado %v", i, d, w*time.Second)
		}
	}
	if b.Attempts() != len(want) {
		t.Fatal("Attempts")
	}
	for range 100 { // muitas falhas seguidas: nunca estoura nem passa do máximo
		if d := b.Next(); d != time.Minute {
			t.Fatalf("depois de muitas tentativas: %v", d)
		}
	}
	b.Reset()
	if b.Attempts() != 0 || b.Next() != time.Second {
		t.Fatal("Reset")
	}
}

func TestJitterStaysWithinBounds(t *testing.T) {
	b := New(time.Second, time.Minute)
	for range 50 {
		b.Reset()
		if d := b.Next(); d < 800*time.Millisecond || d > 1200*time.Millisecond {
			t.Fatalf("±20%% de 1 s: %v", d)
		}
	}
}
