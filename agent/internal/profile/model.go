// Package profile implements the read-profile engine (PROMPT 6.4): profiles are data (YAML/JSON),
// validated against profiles/profile.schema.json, selected by sysObjectID prefix + model regex, and
// evaluated against any snmp.Source.
package profile

//go:generate go run ./gen ../../../profiles/profile.schema.json profile.schema.json

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/dlclark/regexp2"
	"go.yaml.in/yaml/v3"
)

// Placeholder marks proprietary OIDs still to be filled from a real walk; they are never queried.
const Placeholder = "PREENCHER_PELO_WALK"

// Profile is a read profile (same structure as the YAML files; delivered to agents as JSON).
type Profile struct {
	ID             string             `json:"id"`
	Version        int                `json:"version"`
	Description    string             `json:"description,omitempty"`
	Match          *Match             `json:"match,omitempty"`
	Identity       *Identity          `json:"identity,omitempty"`
	Counters       map[string]Counter `json:"counters,omitempty"`
	CounterSources []CounterSource    `json:"counter_sources,omitempty"`
	Rules          *Rules             `json:"rules,omitempty"`
	Status         *Status            `json:"status,omitempty"`
	Supplies       *Supplies          `json:"supplies,omitempty"`
	HTTP           *HTTPReader        `json:"http,omitempty"`
	compiled       map[string]*regexp2.Regexp
}

// Match selects the profile.
type Match struct {
	SysObjectIDPrefix string `json:"sys_object_id_prefix,omitempty"`
	ModelRegex        string `json:"model_regex,omitempty"`
}

// OIDRef is one OID alternative.
type OIDRef struct {
	OID  string `json:"oid"`
	Note string `json:"note,omitempty"`
}

// Identity lists OIDs in order of preference (first non-empty wins).
type Identity struct {
	Serial   []OIDRef `json:"serial,omitempty"`
	Model    []OIDRef `json:"model,omitempty"`
	Firmware []OIDRef `json:"firmware,omitempty"`
}

// Counter defines how one counter is obtained. Exactly one of the fields is set.
type Counter struct {
	OID       string    `json:"oid,omitempty"`
	Sum       []string  `json:"sum,omitempty"`
	FirstOf   []Counter `json:"first_of,omitempty"`
	Expr      string    `json:"expr,omitempty"`
	NameRegex string    `json:"name_regex,omitempty"`
	SumNames  []string  `json:"sum_names,omitempty"`
	Note      string    `json:"note,omitempty"`
}

// NamedTable is a table of counter names + values sharing the same index.
type NamedTable struct {
	NamesOID  string `json:"names_oid"`
	ValuesOID string `json:"values_oid"`
}

// CounterSource is one way of reading counters; the first whose detect_oid answers is used.
type CounterSource struct {
	Name                string             `json:"name"`
	DetectOID           string             `json:"detect_oid,omitempty"`
	NamedTable          *NamedTable        `json:"named_table,omitempty"`
	Counters            map[string]Counter `json:"counters"`
	StoreAllRowsInExtra bool               `json:"store_all_rows_in_extra,omitempty"`
	WalkSubtreeToExtra  string             `json:"walk_subtree_to_extra,omitempty"`
}

// Rules are post-processing rules.
type Rules struct {
	MonoOnlyModelsRegex         string   `json:"mono_only_models_regex,omitempty"`
	ValidateSumTolerancePercent *float64 `json:"validate_sum_tolerance_percent,omitempty"`
}

// Status customizes status normalization.
type Status struct {
	EnergySavingTextRegex string `json:"energy_saving_text_regex,omitempty"`
}

// Supplies controls supply reading.
type Supplies struct {
	UseStandard *bool `json:"use_standard,omitempty"`
}

// HTTPReader reads counters from the printer web page (disabled by default).
type HTTPReader struct {
	Enabled bool   `json:"enabled,omitempty"`
	Path    string `json:"path"`
	Port    int    `json:"port,omitempty"`
	HTTPS   bool   `json:"https,omitempty"`
	Regex   string `json:"regex"`
}

// Sources returns the counter sources, turning a top-level `counters` map into a single source.
func (p *Profile) Sources() []CounterSource {
	if len(p.CounterSources) > 0 {
		return p.CounterSources
	}
	if len(p.Counters) > 0 {
		return []CounterSource{{Name: "default", Counters: p.Counters}}
	}
	return nil
}

// SumTolerancePercent returns the configured tolerance (default 2%, PROMPT 6.5).
func (p *Profile) SumTolerancePercent() float64 {
	if p.Rules != nil && p.Rules.ValidateSumTolerancePercent != nil {
		return *p.Rules.ValidateSumTolerancePercent
	}
	return 2
}

// UseStandardSupplies reports whether the standard supplies table is read (default true).
func (p *Profile) UseStandardSupplies() bool {
	return p.Supplies == nil || p.Supplies.UseStandard == nil || *p.Supplies.UseStandard
}

// regex compiles (and caches) a profile regex. Profiles use .NET/PCRE features such as lookahead,
// so regexp2 is used instead of RE2; a match timeout protects against catastrophic backtracking.
func (p *Profile) regex(expr string) (*regexp2.Regexp, error) {
	if p.compiled == nil {
		p.compiled = map[string]*regexp2.Regexp{}
	}
	if re, ok := p.compiled[expr]; ok {
		return re, nil
	}
	re, err := regexp2.Compile(expr, regexp2.None)
	if err != nil {
		return nil, fmt.Errorf("perfil %s: regex inválida %q: %w", p.ID, expr, err)
	}
	re.MatchTimeout = 200 * time.Millisecond
	p.compiled[expr] = re
	return re, nil
}

// MatchString runs one of the profile's regexes.
func (p *Profile) MatchString(expr, s string) (bool, error) {
	re, err := p.regex(expr)
	if err != nil {
		return false, err
	}
	ok, err := re.MatchString(s)
	if err != nil {
		return false, fmt.Errorf("perfil %s: regex %q: %w", p.ID, expr, err)
	}
	return ok, nil
}

// Compile checks every regex, OID and expression of the profile (beyond the JSON Schema).
func (p *Profile) Compile() error {
	var regexes []string
	if p.Match != nil && p.Match.ModelRegex != "" {
		regexes = append(regexes, p.Match.ModelRegex)
	}
	if p.Rules != nil && p.Rules.MonoOnlyModelsRegex != "" {
		regexes = append(regexes, p.Rules.MonoOnlyModelsRegex)
	}
	if p.Status != nil && p.Status.EnergySavingTextRegex != "" {
		regexes = append(regexes, p.Status.EnergySavingTextRegex)
	}
	if p.HTTP != nil {
		regexes = append(regexes, p.HTTP.Regex)
	}
	for _, src := range p.Sources() {
		for name, c := range src.Counters {
			if err := p.compileCounter(src, name, c, &regexes); err != nil {
				return err
			}
		}
	}
	for _, r := range regexes {
		if _, err := p.regex(r); err != nil {
			return err
		}
	}
	return nil
}

func (p *Profile) compileCounter(src CounterSource, name string, c Counter, regexes *[]string) error {
	if c.Expr != "" {
		if _, err := parseExpr(c.Expr); err != nil {
			return fmt.Errorf("perfil %s, fonte %s, contador %s: %w", p.ID, src.Name, name, err)
		}
	}
	if (c.NameRegex != "" || len(c.SumNames) > 0) && src.NamedTable == nil {
		return fmt.Errorf("perfil %s, fonte %s, contador %s: name_regex/sum_names exigem named_table", p.ID, src.Name, name)
	}
	if c.NameRegex != "" {
		*regexes = append(*regexes, c.NameRegex)
	}
	*regexes = append(*regexes, c.SumNames...)
	for _, alt := range c.FirstOf {
		if err := p.compileCounter(src, name, alt, regexes); err != nil {
			return err
		}
	}
	return nil
}

// FromJSON decodes and validates (schema + regexes/expressions) one profile.
func FromJSON(raw []byte) (*Profile, error) {
	if err := ValidateJSON(raw); err != nil {
		return nil, err
	}
	var p Profile
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, fmt.Errorf("perfil inválido: %w", err)
	}
	if err := p.Compile(); err != nil {
		return nil, err
	}
	return &p, nil
}

// FromYAML converts a YAML profile to JSON and validates it.
func FromYAML(raw []byte) (*Profile, error) {
	var doc any
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("YAML inválido: %w", err)
	}
	js, err := json.Marshal(doc)
	if err != nil {
		return nil, fmt.Errorf("YAML não converte para JSON: %w", err)
	}
	return FromJSON(js)
}

// LoadDir loads every *.yaml profile of a directory.
func LoadDir(dir string) ([]*Profile, error) {
	files, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil {
		return nil, err
	}
	var out []*Profile
	for _, f := range files {
		raw, err := os.ReadFile(filepath.Clean(f))
		if err != nil {
			return nil, err
		}
		p, err := FromYAML(raw)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", filepath.Base(f), err)
		}
		out = append(out, p)
	}
	return out, nil
}

func usable(oid string) bool {
	return oid != "" && !strings.EqualFold(oid, Placeholder)
}
