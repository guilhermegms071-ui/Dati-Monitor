package osinfo

import "testing"

func TestLowerPriority(t *testing.T) {
	if err := LowerPriority(); err != nil {
		t.Fatalf("LowerPriority: %v", err)
	}
}
