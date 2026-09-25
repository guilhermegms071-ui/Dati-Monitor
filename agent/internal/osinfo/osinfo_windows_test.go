//go:build windows

package osinfo

import (
	"strings"
	"testing"
)

func TestWindowsVersionGate(t *testing.T) {
	cases := []struct {
		v         VersionInfo
		name      string
		supported bool
	}{
		{VersionInfo{Major: 6, Minor: 1, Build: 7601}, "Windows 7", false},
		{VersionInfo{Major: 6, Minor: 3, Build: 9600}, "Windows 8.1", false},
		{VersionInfo{Major: 6, Minor: 3, Build: 9600, Server: true}, "Windows Server 2012 R2", false},
		{VersionInfo{Major: 6, Minor: 0, Build: 6002, Server: true}, "Windows Server 2008", false},
		{VersionInfo{Major: 10, Build: 19045}, "Windows 10 (build 19045)", true},
		{VersionInfo{Major: 10, Build: 22631}, "Windows 11 (build 22631)", true},
		{VersionInfo{Major: 10, Build: 14393, Server: true}, "Windows Server (build 14393)", true},
		{VersionInfo{Major: 5, Minor: 1, Build: 2600}, "Windows 5.1 (build 2600)", false},
	}
	for _, c := range cases {
		if c.v.Name() != c.name || c.v.Supported() != c.supported {
			t.Errorf("%+v: %q suportado=%v", c.v, c.v.Name(), c.v.Supported())
		}
		err := checkVersion(c.v)
		if c.supported != (err == nil) {
			t.Errorf("%+v: %v", c.v, err)
		}
		if err != nil && (!strings.Contains(err.Error(), c.name) || !strings.Contains(err.Error(), "Windows 10 ou mais novo")) {
			t.Errorf("mensagem: %v", err)
		}
	}
	if CurrentVersion().Major < 10 {
		t.Fatal("RtlGetVersion deveria ver o Windows real")
	}
}

func TestKeepAwake(t *testing.T) {
	if err := KeepAwake(true); err != nil {
		t.Fatal(err)
	}
	if err := KeepAwake(false); err != nil {
		t.Fatal(err)
	}
}
