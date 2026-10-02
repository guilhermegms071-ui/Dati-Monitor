package protocol

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
)

// schemaNode is the subset of JSON Schema produced by the server (docs/protocol-schemas, generated from
// backend/app/schemas/agent.py) that matters for this contract.
type schemaNode struct {
	Type       any                    `json:"type"`
	Ref        string                 `json:"$ref"`
	AnyOf      []schemaNode           `json:"anyOf"`
	Properties map[string]*schemaNode `json:"properties"`
	Required   []string               `json:"required"`
	Items      *schemaNode            `json:"items"`
	Defs       map[string]*schemaNode `json:"$defs"`
}

func loadSchema(t *testing.T, name string) *schemaNode {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "docs", "protocol-schemas", name+".json"))
	if err != nil {
		t.Fatalf("schema %s: %v", name, err)
	}
	var s schemaNode
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatalf("schema %s: %v", name, err)
	}
	return &s
}

type checker struct {
	t    *testing.T
	msg  string
	defs map[string]*schemaNode
}

func (c *checker) resolve(n *schemaNode) *schemaNode {
	for n != nil && n.Ref != "" {
		n = c.defs[strings.TrimPrefix(n.Ref, "#/$defs/")]
	}
	return n
}

func allowsNull(n *schemaNode) bool {
	if n == nil {
		return true
	}
	if n.Type == "null" {
		return true
	}
	for _, alt := range n.AnyOf {
		if alt.Type == "null" {
			return true
		}
	}
	return false
}

// nonNull returns the alternative of an anyOf that is not "null" (optional nested objects).
func (c *checker) nonNull(n *schemaNode) *schemaNode {
	n = c.resolve(n)
	if n == nil || len(n.AnyOf) == 0 {
		return n
	}
	for i := range n.AnyOf {
		if n.AnyOf[i].Type != "null" {
			return c.resolve(&n.AnyOf[i])
		}
	}
	return n
}

func (c *checker) check(path string, v any, n *schemaNode) {
	n = c.resolve(n)
	if v == nil {
		if !allowsNull(n) {
			c.t.Errorf("%s: %s vai como null, mas o servidor não aceita null nesse campo (use omitempty ou inicialize)", c.msg, path)
		}
		return
	}
	n = c.nonNull(n)
	if n == nil {
		return
	}
	switch val := v.(type) {
	case map[string]any:
		for _, req := range n.Required {
			if _, ok := val[req]; !ok {
				c.t.Errorf("%s: %s.%s é obrigatório no servidor, mas não é enviado", c.msg, path, req)
			}
		}
		for key, child := range val {
			if prop, ok := n.Properties[key]; ok {
				c.check(path+"."+key, child, prop)
			}
		}
	case []any:
		for i, item := range val {
			if n.Items != nil {
				c.check(fmt.Sprintf("%s[%d]", path, i), item, n.Items)
			}
		}
	}
}

// TestMessagesMatchServerSchemas: every message the agent or the watchdog SENDS, as Go marshals it
// (zero values included — a nil slice or map becomes JSON null), must be accepted by the server.
func TestMessagesMatchServerSchemas(t *testing.T) {
	now := time.Now().UTC()
	sent := map[string]any{
		"EnrollRequest":        EnrollRequest{},
		"EnrollCheckRequest":   EnrollCheckRequest{},
		"TokenRequest":         TokenRequest{},
		"HeartbeatRequest":     HeartbeatRequest{Ts: now},
		"SuggestRangesRequest": SuggestRangesRequest{},
		"ReadingsRequest": ReadingsRequest{Items: []Item{
			{Kind: "reading", ReadAt: now, Reading: &ReadingPayload{}},
			{Kind: "supplies", ReadAt: now, Supplies: []printer.Supply{{}}},
			{Kind: "status", ReadAt: now, Status: &printer.StatusResult{}},
			{Kind: "event", ReadAt: now, Event: &EventPayload{}},
			{Kind: "attributes", ReadAt: now, Attributes: &printer.Attributes{}},
			{Kind: "attributes", ReadAt: now, Attributes: &printer.Attributes{
				Storage: []printer.Storage{{}}, Subsystems: []printer.Subsystem{{}},
				Parts: []printer.Part{{Part: "drum", Unit: "percent"}},
			}},
			{Kind: "status", ReadAt: now, Status: &printer.StatusResult{Alerts: []printer.Alert{{}}}},
			{Kind: "reading", ReadAt: now, Reading: &ReadingPayload{
				CounterLines: map[string]profile.Line{"x": {Kind: "print", ColorMode: "mono", Size: "a4"}},
			}},
		}},
		"CommandUpdate":            CommandUpdate{},
		"Hello":                    Hello{},
		"WatchdogHeartbeatRequest": WatchdogHeartbeatRequest{Ts: now},
		"WsMessage":                WSMessage{},
		"WebResponseStart":         WebResponseStart{},
		"WebChunk":                 WebChunk{},
		"WebError":                 WebError{},
	}
	for name, msg := range sent {
		schema := loadSchema(t, name)
		raw, err := json.Marshal(msg)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		var v any
		if err := json.Unmarshal(raw, &v); err != nil {
			t.Fatal(err)
		}
		c := &checker{t: t, msg: name, defs: schema.Defs}
		c.check(name, v, schema)
	}
}
