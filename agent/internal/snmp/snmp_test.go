package snmp

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"
)

func TestDecodeText(t *testing.T) {
	cases := map[string]string{
		"Total 1":              "Total 1",
		"54 6F 74 61 6C 20 31": "Total 1",
		"0x546F74616C":         "Total",
		"54:6f:74:61:6c":       "Total",
		"CAFE":                 "CAFE", // sem separador: não é hex
		"00 01 02":             "00 01 02",
		"Pronto\x00\x00":       "Pronto",
		"12 34 zz":             "12 34 zz",
	}
	for in, want := range cases {
		if got := DecodeText([]byte(in)); got != want {
			t.Errorf("%q -> %q, want %q", in, got, want)
		}
	}
	if got := DecodeText([]byte{0x49, 0x6d, 0x70, 0x72, 0x65, 0x73, 0x73, 0xe3, 0x6f}); got != "Impressão" {
		t.Errorf("latin-1: %q", got)
	}
}

func TestOIDHelpers(t *testing.T) {
	if !HasPrefix("1.3.6.1.4.1.1602.1", ".1.3.6.1.4.1.1602") || HasPrefix("1.3.6.1.4.1.16020", "1.3.6.1.4.1.1602") {
		t.Fatal("HasPrefix")
	}
	if CompareOID("1.3.6.1.2", "1.3.6.1.10") >= 0 || CompareOID("1.3.6", "1.3.6.1") >= 0 || CompareOID("1.2", "1.2") != 0 {
		t.Fatal("CompareOID")
	}
	if !ValidOID("1.3.6") || ValidOID("1") || ValidOID("1..3") || ValidOID("1.x") {
		t.Fatal("ValidOID")
	}
	if FormatMAC([]byte{0, 0x15, 0x5d, 0, 0, 5}) != "00:15:5D:00:00:05" || FormatMAC(make([]byte, 6)) != "" || FormatMAC([]byte{1}) != "" {
		t.Fatal("FormatMAC")
	}
}

func TestSnmprecRoundTripAndMemSource(t *testing.T) {
	rec := strings.Join([]string{
		"1.3.6.1.2.1.1.2.0|6|1.3.6.1.4.1.1602.4.7",
		"# comentário",
		"",
		"1.3.6.1.2.1.43.10.2.1.4.1.1|65|150000",
		"1.3.6.1.2.1.1.1.0|4|Canon iR",
		"1.3.6.1.4.1.1602.1.11.2.1.1.2.1|4x|546f74616c2031",
		"1.3.6.1.2.1.25.3.5.1.2.1|4x|0c00",
		"1.3.6.1.2.1.4.20.1.1.1|64|10.0.0.1",
		"1.3.6.1.2.1.1.3.0|67|123",
		"1.3.6.1.2.1.43.11.1.1.9.1.1|2|-3",
		"1.3.6.1.2.1.99.1|70|9000000000",
		"1.3.6.1.2.1.99.2|66|7",
		"1.3.6.1.2.1.99.3|68|0a0b",
		"1.3.6.1.2.1.99.4|5|",
	}, "\n")
	pdus, err := ParseSnmprec(strings.NewReader(rec))
	if err != nil {
		t.Fatal(err)
	}
	src := NewMemSource(pdus)
	vals, err := src.Get(context.Background(), []string{".1.3.6.1.2.1.1.1.0", "1.3.6.1.4.1.1602.1.11.2.1.1.2.1", "9.9.9", "1.3.6.1.2.1.43.11.1.1.9.1.1"})
	if err != nil {
		t.Fatal(err)
	}
	if vals[0].String() != "Canon iR" || vals[1].String() != "Total 1" || vals[2].Exists() || vals[3].Int != -3 {
		t.Fatalf("%+v", vals)
	}
	var walked []string
	if err := src.Walk(context.Background(), "1.3.6.1.2.1.1", func(p PDU) error {
		walked = append(walked, p.OID)
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if strings.Join(walked, ",") != "1.3.6.1.2.1.1.1.0,1.3.6.1.2.1.1.2.0,1.3.6.1.2.1.1.3.0" {
		t.Fatalf("walk: %v", walked)
	}
	stop := errors.New("stop")
	if err := src.Walk(context.Background(), "1.3.6", func(PDU) error { return stop }); !errors.Is(err, stop) {
		t.Fatal("walk deveria propagar o erro do callback")
	}
	var buf bytes.Buffer
	if err := WriteSnmprec(&buf, pdus); err != nil {
		t.Fatal(err)
	}
	again, err := ParseSnmprec(&buf)
	if err != nil {
		t.Fatal(err)
	}
	if len(again) != len(pdus)-1 { // o Null não é gravado
		t.Fatalf("ida e volta perdeu linhas: %d vs %d", len(again), len(pdus))
	}
	if src.Len() != len(pdus) {
		t.Fatal("Len")
	}
	for _, bad := range []string{"1.3|4", "abc|4|x", "1.3|99|x", "1.3|2|x", "1.3|4x|zz"} {
		if _, err := ParseSnmprec(strings.NewReader(bad)); err == nil {
			t.Errorf("%q deveria falhar", bad)
		}
	}
}

func TestPDUNumberAndString(t *testing.T) {
	if n, ok := (PDU{Kind: KindOctetString, Bytes: []byte(" 123 ")}).Number(); !ok || n != 123 {
		t.Fatal("número em texto")
	}
	if _, ok := (PDU{Kind: KindOctetString, Bytes: []byte("abc")}).Number(); ok {
		t.Fatal("texto não numérico")
	}
	if (PDU{Kind: KindCounter64, Int: 5}).String() != "5" || (PDU{Kind: KindIPAddress, Text: "1.2.3.4"}).String() != "1.2.3.4" || (PDU{}).String() != "" {
		t.Fatal("String")
	}
}

func TestV3ParamsValidation(t *testing.T) {
	good := []Credential{
		{Version: "v3", V3Username: "u"},
		{Version: "v3", V3Username: "u", V3AuthProto: "SHA", V3AuthPass: "12345678"},
		{Version: "v3", V3Username: "u", V3AuthProto: "SHA256", V3AuthPass: "12345678", V3PrivProto: "AES256", V3PrivPass: "12345678"},
	}
	for _, c := range good {
		if _, _, err := v3Params(c); err != nil {
			t.Errorf("%+v: %v", c, err)
		}
	}
	bad := []Credential{
		{Version: "v3"},
		{Version: "v3", V3Username: "u", V3AuthProto: "MD5"},
		{Version: "v3", V3Username: "u", V3PrivProto: "AES"},
		{Version: "v3", V3Username: "u", V3AuthProto: "SHA", V3PrivProto: "DES"},
	}
	for _, c := range bad {
		if _, _, err := v3Params(c); err == nil {
			t.Errorf("%+v deveria falhar", c)
		}
	}
	if _, err := Dial("127.0.0.1", 161, Credential{Version: "v9"}, DefaultOptions()); err == nil {
		t.Error("versão inválida deveria falhar")
	}
}
