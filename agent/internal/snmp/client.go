package snmp

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"net"
	"strings"
	"time"

	"github.com/gosnmp/gosnmp"
)

// Credential is one entry of the site's SNMP credential list (tried in order).
type Credential struct {
	ID          string `json:"id"`
	Version     string `json:"version"` // v1, v2c, v3
	Community   string `json:"community,omitempty"`
	V3Username  string `json:"v3_username,omitempty"`
	V3AuthProto string `json:"v3_auth_protocol,omitempty"` // SHA, SHA256 ("" = noAuth)
	V3AuthPass  string `json:"v3_auth_password,omitempty"`
	V3PrivProto string `json:"v3_priv_protocol,omitempty"` // AES, AES256 ("" = noPriv)
	V3PrivPass  string `json:"v3_priv_password,omitempty"`
	V3Context   string `json:"v3_context,omitempty"` // contextName (vazio na maioria das impressoras)
}

// Options controls timeouts and retries (PROMPT 4.5: 1500 ms, 1 retry).
type Options struct {
	Timeout        time.Duration
	Retries        int
	MaxOIDsPerGet  int
	MaxRepetitions uint32
}

// DefaultOptions are the collector defaults.
func DefaultOptions() Options {
	return Options{Timeout: 1500 * time.Millisecond, Retries: 1, MaxOIDsPerGet: 20, MaxRepetitions: 20}
}

// Client talks to one device with one credential.
type Client struct {
	g    *gosnmp.GoSNMP
	opts Options
}

// Dial prepares a client for host:port (UDP is connectionless; this only opens the socket).
func Dial(host string, port int, cred Credential, opts Options) (*Client, error) {
	if opts.Timeout <= 0 {
		opts = DefaultOptions()
	}
	if opts.MaxOIDsPerGet <= 0 {
		opts.MaxOIDsPerGet = 20
	}
	if opts.MaxRepetitions == 0 {
		opts.MaxRepetitions = 20
	}
	g := &gosnmp.GoSNMP{
		Target:             host,
		Port:               uint16(port), //nolint:gosec // G115: port validated by callers (1..65535)
		Transport:          "udp",
		Timeout:            opts.Timeout,
		Retries:            opts.Retries,
		ExponentialTimeout: false,
		MaxOids:            opts.MaxOIDsPerGet,
		MaxRepetitions:     opts.MaxRepetitions,
	}
	switch strings.ToLower(cred.Version) {
	case "v1", "1":
		g.Version, g.Community = gosnmp.Version1, cred.Community
	case "v2c", "2c", "":
		g.Version, g.Community = gosnmp.Version2c, cred.Community
	case "v3", "3":
		g.Version = gosnmp.Version3
		g.SecurityModel = gosnmp.UserSecurityModel
		params, flags, err := v3Params(cred)
		if err != nil {
			return nil, err
		}
		g.MsgFlags, g.SecurityParameters = flags, params
		g.ContextName = cred.V3Context
	default:
		return nil, fmt.Errorf("versão SNMP desconhecida: %q", cred.Version)
	}
	if err := g.Connect(); err != nil {
		return nil, fmt.Errorf("abrir socket SNMP para %s: %w", net.JoinHostPort(host, fmt.Sprint(port)), err)
	}
	return &Client{g: g, opts: opts}, nil
}

func v3Params(c Credential) (*gosnmp.UsmSecurityParameters, gosnmp.SnmpV3MsgFlags, error) {
	if c.V3Username == "" {
		return nil, 0, errors.New("SNMPv3 exige usuário")
	}
	p := &gosnmp.UsmSecurityParameters{UserName: c.V3Username, AuthenticationProtocol: gosnmp.NoAuth, PrivacyProtocol: gosnmp.NoPriv}
	flags := gosnmp.NoAuthNoPriv
	switch strings.ToUpper(c.V3AuthProto) {
	case "":
	case "SHA":
		p.AuthenticationProtocol = gosnmp.SHA
	case "SHA256":
		p.AuthenticationProtocol = gosnmp.SHA256
	default:
		return nil, 0, fmt.Errorf("autenticação SNMPv3 não suportada: %q", c.V3AuthProto)
	}
	if p.AuthenticationProtocol != gosnmp.NoAuth {
		p.AuthenticationPassphrase = c.V3AuthPass
		flags = gosnmp.AuthNoPriv
	}
	switch strings.ToUpper(c.V3PrivProto) {
	case "":
	case "AES":
		p.PrivacyProtocol = gosnmp.AES
	case "AES256":
		// Blumenthal (mesma extensão de chave do Net-SNMP, usada pela maioria das impressoras).
		p.PrivacyProtocol = gosnmp.AES256
	default:
		return nil, 0, fmt.Errorf("criptografia SNMPv3 não suportada: %q", c.V3PrivProto)
	}
	if p.PrivacyProtocol != gosnmp.NoPriv {
		if p.AuthenticationProtocol == gosnmp.NoAuth {
			return nil, 0, errors.New("SNMPv3 com criptografia exige autenticação")
		}
		p.PrivacyPassphrase = c.V3PrivPass
		flags = gosnmp.AuthPriv
	}
	return p, flags, nil
}

// Close releases the socket.
func (c *Client) Close() error {
	if c.g.Conn == nil {
		return nil
	}
	return c.g.Conn.Close()
}

func wrapErr(err error) error {
	if err == nil {
		return nil
	}
	var nerr net.Error
	if errors.As(err, &nerr) && nerr.Timeout() || strings.Contains(strings.ToLower(err.Error()), "timeout") {
		return fmt.Errorf("%w: %s", ErrTimeout, err.Error())
	}
	return err
}

// Get implements Source (chunked to MaxOIDsPerGet).
func (c *Client) Get(ctx context.Context, oids []string) ([]PDU, error) {
	out := make([]PDU, 0, len(oids))
	for start := 0; start < len(oids); start += c.opts.MaxOIDsPerGet {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		end := min(start+c.opts.MaxOIDsPerGet, len(oids))
		chunk := make([]string, end-start)
		for i, o := range oids[start:end] {
			chunk[i] = "." + NormalizeOID(o)
		}
		pkt, err := c.g.Get(chunk)
		if err != nil {
			return nil, wrapErr(err)
		}
		if pkt.Error != gosnmp.NoError && c.g.Version == gosnmp.Version1 {
			// SNMPv1 responde noSuchName para o pacote inteiro: repete OID a OID.
			for _, oid := range chunk {
				single, err := c.g.Get([]string{oid})
				if err != nil {
					return nil, wrapErr(err)
				}
				if single.Error != gosnmp.NoError || len(single.Variables) == 0 {
					out = append(out, PDU{OID: NormalizeOID(oid), Kind: KindNoSuch})
					continue
				}
				out = append(out, convert(single.Variables[0]))
			}
			continue
		}
		if pkt.Error != gosnmp.NoError {
			return nil, fmt.Errorf("erro SNMP %s", pkt.Error)
		}
		for _, v := range pkt.Variables {
			out = append(out, convert(v))
		}
	}
	return out, nil
}

// Walk implements Source (GETBULK for v2c/v3, GETNEXT for v1).
func (c *Client) Walk(ctx context.Context, root string, fn func(PDU) error) error {
	walk := c.g.BulkWalk
	if c.g.Version == gosnmp.Version1 {
		walk = c.g.Walk
	}
	root = NormalizeOID(root)
	err := walk("."+root, func(v gosnmp.SnmpPDU) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		p := convert(v)
		if !p.Exists() || !HasPrefix(p.OID, root) || p.OID == root {
			return nil
		}
		return fn(p)
	})
	return wrapErr(err)
}

func convert(v gosnmp.SnmpPDU) PDU {
	p := PDU{OID: NormalizeOID(v.Name)}
	switch v.Type {
	case gosnmp.Integer:
		p.Kind, p.Int = KindInteger, toInt64(v.Value)
	case gosnmp.Counter32:
		p.Kind, p.Int = KindCounter32, toInt64(v.Value)
	case gosnmp.Gauge32, gosnmp.Uinteger32:
		p.Kind, p.Int = KindGauge32, toInt64(v.Value)
	case gosnmp.TimeTicks:
		p.Kind, p.Int = KindTimeTicks, toInt64(v.Value)
	case gosnmp.Counter64:
		p.Kind, p.Int = KindCounter64, toInt64(v.Value)
	case gosnmp.OctetString, gosnmp.BitString:
		p.Kind = KindOctetString
		if b, ok := v.Value.([]byte); ok {
			p.Bytes = b
		}
	case gosnmp.Opaque:
		p.Kind = KindOpaque
		if b, ok := v.Value.([]byte); ok {
			p.Bytes = b
		}
	case gosnmp.ObjectIdentifier:
		p.Kind = KindOID
		if s, ok := v.Value.(string); ok {
			p.Text = NormalizeOID(s)
		}
	case gosnmp.IPAddress:
		p.Kind = KindIPAddress
		if s, ok := v.Value.(string); ok {
			p.Text = s
		}
	default:
		p.Kind = KindNoSuch
	}
	return p
}

func toInt64(v any) int64 {
	b := gosnmp.ToBigInt(v)
	if b == nil || !b.IsInt64() {
		if b != nil && b.Cmp(big.NewInt(0)) > 0 {
			return int64(^uint64(0) >> 1)
		}
		return 0
	}
	return b.Int64()
}
