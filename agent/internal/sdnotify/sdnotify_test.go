package sdnotify

import "testing"

func TestNoOpOutsideSystemd(t *testing.T) {
	t.Setenv("NOTIFY_SOCKET", "")
	if sent, err := Send("READY=1"); sent || err != nil {
		t.Fatalf("%v %v", sent, err)
	}
	if Ready() != nil || Watchdog() != nil || Stopping() != nil {
		t.Fatal("fora do systemd nada pode falhar")
	}
}
