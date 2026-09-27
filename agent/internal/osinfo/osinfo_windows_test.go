//go:build windows

package osinfo

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// TestMain doubles as a child process that prints CurrentVersion (see TestVersionIgnoresCompatShim).
func TestMain(m *testing.M) {
	if os.Getenv("DM_OSINFO_PRINT_VERSION") == "1" {
		v := CurrentVersion()
		fmt.Printf("%d.%d.%d\n", v.Major, v.Minor, v.Build)
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// Modo de compatibilidade do Windows 7 no processo (o que um usuário faria nas propriedades do .exe)
// não pode fazer um Windows 10 ser recusado no cadastro.
func TestVersionIgnoresCompatShim(t *testing.T) {
	want := CurrentVersion()
	if want.Major < 10 {
		t.Fatalf("versão real deveria ser 10+: %+v", want)
	}
	exe, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(exe, "-test.run=^$") //nolint:gosec // G204: é o próprio binário deste teste

	cmd.Env = append(os.Environ(), "DM_OSINFO_PRINT_VERSION=1", "__COMPAT_LAYER=Win7RTM")
	out, err := cmd.Output()
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.TrimSpace(string(out)); got != fmt.Sprintf("%d.%d.%d", want.Major, want.Minor, want.Build) {
		t.Fatalf("com __COMPAT_LAYER=Win7RTM a versão virou %q (real %+v)", got, want)
	}
}

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
		t.Fatal("CurrentVersion deveria ver o Windows real")
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
