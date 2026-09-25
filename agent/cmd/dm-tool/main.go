// Command dm-tool: ferramentas de suporte do coletor.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/cli"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

func main() {
	app := cli.App{Binary: "dm-tool", Description: "ferramentas de suporte do coletor", Commands: map[string]cli.Command{
		"walk": {Summary: "walk SNMP completo de uma impressora para arquivo .snmprec", Run: cmdWalk},
	}}
	os.Exit(app.Main(os.Args[1:], os.Stdout, os.Stderr))
}

func cmdWalk(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("walk", flag.ContinueOnError)
	fs.SetOutput(stderr)
	ip := fs.String("ip", "", "IP da impressora")
	port := fs.Int("port", 161, "porta SNMP")
	version := fs.String("version", "v2c", "versão SNMP: v1, v2c ou v3")
	community := fs.String("community", "public", "comunidade (v1/v2c)")
	user := fs.String("v3-user", "", "usuário SNMPv3")
	authProto := fs.String("v3-auth", "", "autenticação SNMPv3: SHA ou SHA256")
	authPass := fs.String("v3-auth-pass", "", "senha de autenticação SNMPv3")
	privProto := fs.String("v3-priv", "", "criptografia SNMPv3: AES ou AES256")
	privPass := fs.String("v3-priv-pass", "", "senha de criptografia SNMPv3")
	root := fs.String("root", "1.3.6.1", "subárvore a percorrer")
	out := fs.String("out", "", "arquivo de saída .snmprec")
	timeoutMS := fs.Int("timeout-ms", 3000, "timeout por requisição (ms)")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if *ip == "" || *out == "" {
		_, _ = fmt.Fprintln(stderr, "ERRO: informe --ip e --out")
		return 2
	}
	if !snmp.ValidOID(*root) {
		_, _ = fmt.Fprintf(stderr, "ERRO: --root inválido: %q\n", *root)
		return 2
	}
	cred := snmp.Credential{
		ID: "cli", Version: *version, Community: *community, V3Username: *user,
		V3AuthProto: *authProto, V3AuthPass: *authPass, V3PrivProto: *privProto, V3PrivPass: *privPass,
	}
	opts := snmp.DefaultOptions()
	opts.Timeout = time.Duration(*timeoutMS) * time.Millisecond
	opts.Retries = 2
	n, err := Walk(context.Background(), *ip, *port, cred, opts, *root, *out, func(count int) {
		if count%500 == 0 {
			_, _ = fmt.Fprintf(stderr, "  %d OIDs...\n", count)
		}
	})
	if err != nil {
		_, _ = fmt.Fprintf(stderr, "ERRO: %v\n", err)
		return 1
	}
	_, _ = fmt.Fprintf(stdout, "%d OIDs gravados em %s\n", n, *out)
	return 0
}

// Walk walks root on ip:port and writes an snmpsim recording to path. The file is written only when
// the walk completes (partial walks never overwrite a good recording).
func Walk(ctx context.Context, ip string, port int, cred snmp.Credential, opts snmp.Options, root, path string,
	progress func(int)) (int, error) {
	c, err := snmp.Dial(ip, port, cred, opts)
	if err != nil {
		return 0, err
	}
	defer func() { _ = c.Close() }()
	var pdus []snmp.PDU
	err = c.Walk(ctx, root, func(p snmp.PDU) error {
		pdus = append(pdus, p)
		if progress != nil {
			progress(len(pdus))
		}
		return nil
	})
	if err != nil {
		return 0, fmt.Errorf("walk interrompido após %d OIDs: %w", len(pdus), err)
	}
	if len(pdus) == 0 {
		return 0, fmt.Errorf("nenhum OID retornado sob %s (credencial ou subárvore incorreta?)", root)
	}
	tmp := path + ".tmp"
	f, err := os.Create(tmp) //nolint:gosec // G304: caminho escolhido pelo operador
	if err != nil {
		return 0, err
	}
	if err := snmp.WriteSnmprec(f, pdus); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return 0, err
	}
	if err := f.Close(); err != nil {
		return 0, err
	}
	return len(pdus), os.Rename(tmp, path)
}
