package svc

import "testing"

func TestExeFromCommandLine(t *testing.T) {
	cases := map[string]string{
		`"C:\Program Files\DatiMonitor\dm-agent.exe" service`: `C:\Program Files\DatiMonitor\dm-agent.exe`,
		`C:\DatiMonitor\dm-agent.exe service --data-dir C:\x`: `C:\DatiMonitor\dm-agent.exe`,
		`C:\Program Files\DatiMonitor\dm-agent.EXE service`:   `C:\Program Files\DatiMonitor\dm-agent.EXE`,
		`/usr/local/bin/dm-agent service`:                     `/usr/local/bin/dm-agent`,
		`"C:\sem aspas finais\dm-agent.exe`:                   `C:\sem aspas finais\dm-agent.exe`,
	}
	for in, want := range cases {
		if got := exeFromCommandLine(in); got != want {
			t.Errorf("%q → %q, esperado %q", in, got, want)
		}
	}
}

func TestExecStartPath(t *testing.T) {
	prop := "{ path=/usr/local/bin/dm-agent ; argv[]=/usr/local/bin/dm-agent service ; ignore_errors=no ; start_time=[n/a] }"
	if got := execStartPath(prop); got != "/usr/local/bin/dm-agent" {
		t.Fatalf("ExecStart: %q", got)
	}
	if got := execStartPath(""); got != "" {
		t.Fatalf("vazio: %q", got)
	}
}
