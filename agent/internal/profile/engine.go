package profile

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"sort"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// Standard OIDs (PROMPT 6.2) used when a profile does not define identity.
const (
	OIDSerial      = "1.3.6.1.2.1.43.5.1.1.17.1"
	OIDModel       = "1.3.6.1.2.1.25.3.2.1.3.1"
	OIDLifeCount   = "1.3.6.1.2.1.43.10.2.1.4.1.1"
	standardSource = "standard"
	maxExtraRows   = 2000
)

// NormalizedCounters are the counter fields stored in dedicated columns of `readings`.
var NormalizedCounters = []string{
	"total", "mono", "color", "mono_large", "color_large",
	"copy_mono", "copy_color", "print_mono", "print_color", "scan", "fax",
}

// IdentityResult holds the device identity found by the profile.
type IdentityResult struct {
	Serial   string `json:"serial"`
	Model    string `json:"model"`
	Firmware string `json:"firmware,omitempty"`
}

// Result of evaluating a profile's counters.
type Result struct {
	ProfileID           string           `json:"profile_key"`
	ProfileVersion      int              `json:"profile_version"`
	Source              string           `json:"counter_source"`
	Counters            map[string]int64 `json:"counters"`
	Extra               map[string]any   `json:"extra,omitempty"`
	Unresolved          []string         `json:"unresolved,omitempty"`
	MonoOnly            bool             `json:"mono_only"`
	SumTolerancePercent float64          `json:"sum_tolerance_percent"`
}

// ResolveIdentity returns serial/model/firmware using the profile's OID lists (in order of
// preference), falling back to the standard OIDs.
func ResolveIdentity(ctx context.Context, src snmp.Source, p *Profile) (IdentityResult, error) {
	var serialRefs, modelRefs, fwRefs []OIDRef
	if p != nil && p.Identity != nil {
		serialRefs, modelRefs, fwRefs = p.Identity.Serial, p.Identity.Model, p.Identity.Firmware
	}
	if len(serialRefs) == 0 {
		serialRefs = []OIDRef{{OID: OIDSerial}}
	}
	if len(modelRefs) == 0 {
		modelRefs = []OIDRef{{OID: OIDModel}}
	}
	var oids []string
	for _, list := range [][]OIDRef{serialRefs, modelRefs, fwRefs} {
		for _, r := range list {
			if usable(r.OID) {
				oids = append(oids, snmp.NormalizeOID(r.OID))
			}
		}
	}
	values, err := getMap(ctx, src, oids)
	if err != nil {
		return IdentityResult{}, err
	}
	first := func(refs []OIDRef) string {
		for _, r := range refs {
			if v, ok := values[snmp.NormalizeOID(r.OID)]; ok && v.Exists() {
				if s := strings.TrimSpace(v.String()); s != "" {
					return s
				}
			}
		}
		return ""
	}
	return IdentityResult{Serial: first(serialRefs), Model: first(modelRefs), Firmware: first(fwRefs)}, nil
}

// Evaluate reads the counters of a device using the profile. `model` is the identity model used by
// rules.mono_only_models_regex. SNMP transport errors (timeouts) are returned; counters that simply
// do not exist are listed in Result.Unresolved.
func Evaluate(ctx context.Context, src snmp.Source, p *Profile, model string) (*Result, error) {
	if p == nil {
		return nil, errors.New("nenhum perfil disponível")
	}
	res := &Result{
		ProfileID: p.ID, ProfileVersion: p.Version, Counters: map[string]int64{},
		Extra: map[string]any{}, SumTolerancePercent: p.SumTolerancePercent(),
	}
	sources := p.Sources()
	chosen := -1
	for i, s := range sources {
		ok, err := detect(ctx, src, s)
		if err != nil {
			return nil, err
		}
		if ok {
			chosen = i
			break
		}
	}
	if chosen < 0 {
		res.Source = ""
		res.Unresolved = append(res.Unresolved, "total")
		return res, nil
	}
	s := sources[chosen]
	res.Source = s.Name
	if err := evalSource(ctx, src, p, s, res); err != nil {
		return nil, err
	}
	// Se a fonte escolhida não trouxe o total, a fonte "standard" (último recurso) completa o total.
	if _, ok := res.Counters["total"]; !ok {
		for _, fb := range sources[chosen+1:] {
			if fb.Name != standardSource {
				continue
			}
			fbRes := &Result{Counters: map[string]int64{}, Extra: map[string]any{}}
			if err := evalSource(ctx, src, p, fb, fbRes); err != nil {
				return nil, err
			}
			if v, ok := fbRes.Counters["total"]; ok {
				res.Counters["total"] = v
				res.Extra["total_from"] = standardSource
			}
		}
	}
	if err := applyRules(p, res, model); err != nil {
		return nil, err
	}
	res.Unresolved = res.Unresolved[:0]
	for _, name := range sortedKeys(countersOf(s)) {
		if _, ok := res.Counters[name]; !ok {
			res.Unresolved = append(res.Unresolved, name)
		}
	}
	return res, nil
}

func countersOf(s CounterSource) map[string]Counter { return s.Counters }

func detect(ctx context.Context, src snmp.Source, s CounterSource) (bool, error) {
	if s.DetectOID == "" {
		return true, nil
	}
	if !usable(s.DetectOID) {
		return false, nil
	}
	vals, err := src.Get(ctx, []string{s.DetectOID})
	if err != nil {
		return false, err
	}
	if len(vals) == 1 && vals[0].Exists() {
		return true, nil
	}
	found := false
	errStop := errors.New("stop")
	err = src.Walk(ctx, s.DetectOID, func(snmp.PDU) error {
		found = true
		return errStop
	})
	if err != nil && !errors.Is(err, errStop) {
		return false, err
	}
	return found, nil
}

type tableRow struct {
	name  string
	value int64
}

func evalSource(ctx context.Context, src snmp.Source, p *Profile, s CounterSource, res *Result) error {
	// 1) OIDs diretos (oid/sum/first_of) numa única rodada de GET.
	var oids []string
	for _, c := range s.Counters {
		collectOIDs(c, &oids)
	}
	values, err := getMap(ctx, src, oids)
	if err != nil {
		return err
	}
	// 2) Tabela nomeada, se houver.
	var rows []tableRow
	if s.NamedTable != nil && usable(s.NamedTable.NamesOID) && usable(s.NamedTable.ValuesOID) {
		rows, err = readNamedTable(ctx, src, s.NamedTable)
		if err != nil {
			return err
		}
	}
	// 3) Contadores não calculados.
	var exprs []string
	for name, c := range s.Counters {
		if c.Expr != "" {
			exprs = append(exprs, name)
			continue
		}
		if v, ok, err := resolve(p, c, values, rows); err != nil {
			return err
		} else if ok {
			res.Counters[name] = v
		}
	}
	// 4) Expressões (podem depender umas das outras): resolve até não haver progresso.
	sort.Strings(exprs)
	for progress := true; progress && len(exprs) > 0; {
		progress = false
		for i := 0; i < len(exprs); i++ {
			node, err := parseExpr(s.Counters[exprs[i]].Expr)
			if err != nil {
				return err
			}
			v, err := node.eval(res.Counters)
			var missing errMissingRef
			if errors.As(err, &missing) {
				continue
			}
			if err != nil {
				return fmt.Errorf("contador %s: %w", exprs[i], err)
			}
			res.Counters[exprs[i]] = v
			exprs = slices.Delete(exprs, i, i+1)
			i--
			progress = true
		}
	}
	// 5) Valores brutos em extra.
	if s.StoreAllRowsInExtra {
		all := map[string]any{}
		if s.NamedTable != nil {
			for _, r := range rows {
				all[r.name] = r.value
			}
		} else if usable(s.DetectOID) {
			if err := walkInto(ctx, src, s.DetectOID, all); err != nil {
				return err
			}
		}
		res.Extra[s.Name] = all
	}
	if usable(s.WalkSubtreeToExtra) {
		sub := map[string]any{}
		if err := walkInto(ctx, src, s.WalkSubtreeToExtra, sub); err != nil {
			return err
		}
		res.Extra[s.Name] = sub
	}
	return nil
}

func collectOIDs(c Counter, out *[]string) {
	if usable(c.OID) {
		*out = append(*out, snmp.NormalizeOID(c.OID))
	}
	for _, o := range c.Sum {
		if usable(o) {
			*out = append(*out, snmp.NormalizeOID(o))
		}
	}
	for _, alt := range c.FirstOf {
		collectOIDs(alt, out)
	}
}

func resolve(p *Profile, c Counter, values map[string]snmp.PDU, rows []tableRow) (int64, bool, error) {
	switch {
	case c.OID != "":
		if !usable(c.OID) {
			return 0, false, nil
		}
		v, ok := values[snmp.NormalizeOID(c.OID)]
		if !ok || !v.Exists() {
			return 0, false, nil
		}
		n, ok := v.Number()
		return n, ok, nil
	case len(c.Sum) > 0:
		// Soma só quando TODAS as parcelas existem (soma parcial subcontaria as páginas).
		var total int64
		for _, o := range c.Sum {
			v, ok := values[snmp.NormalizeOID(o)]
			if !usable(o) || !ok || !v.Exists() {
				return 0, false, nil
			}
			n, ok := v.Number()
			if !ok {
				return 0, false, nil
			}
			total += n
		}
		return total, true, nil
	case len(c.FirstOf) > 0:
		for _, alt := range c.FirstOf {
			v, ok, err := resolve(p, alt, values, rows)
			if err != nil || ok {
				return v, ok, err
			}
		}
		return 0, false, nil
	case c.NameRegex != "":
		for _, r := range rows {
			ok, err := p.MatchString(c.NameRegex, r.name)
			if err != nil {
				return 0, false, err
			}
			if ok {
				return r.value, true, nil
			}
		}
		return 0, false, nil
	case len(c.SumNames) > 0:
		var total int64
		for _, pattern := range c.SumNames {
			found := false
			for _, r := range rows {
				ok, err := p.MatchString(pattern, r.name)
				if err != nil {
					return 0, false, err
				}
				if ok {
					total += r.value
					found = true
					break
				}
			}
			if !found {
				return 0, false, nil
			}
		}
		return total, true, nil
	}
	return 0, false, nil
}

func readNamedTable(ctx context.Context, src snmp.Source, t *NamedTable) ([]tableRow, error) {
	names := map[string]string{}
	namesRoot := snmp.NormalizeOID(t.NamesOID)
	err := src.Walk(ctx, namesRoot, func(pdu snmp.PDU) error {
		names[strings.TrimPrefix(pdu.OID, namesRoot+".")] = strings.TrimSpace(pdu.String())
		return nil
	})
	if err != nil {
		return nil, err
	}
	var rows []tableRow
	valuesRoot := snmp.NormalizeOID(t.ValuesOID)
	err = src.Walk(ctx, valuesRoot, func(pdu snmp.PDU) error {
		idx := strings.TrimPrefix(pdu.OID, valuesRoot+".")
		name, ok := names[idx]
		if !ok {
			return nil
		}
		if n, ok := pdu.Number(); ok {
			rows = append(rows, tableRow{name: name, value: n})
		}
		return nil
	})
	return rows, err
}

func walkInto(ctx context.Context, src snmp.Source, root string, out map[string]any) error {
	root = snmp.NormalizeOID(root)
	return src.Walk(ctx, root, func(pdu snmp.PDU) error {
		if len(out) >= maxExtraRows {
			return nil
		}
		key := strings.TrimPrefix(pdu.OID, root+".")
		if n, ok := pdu.Number(); ok && pdu.IsNumeric() {
			out[key] = n
		} else {
			out[key] = pdu.String()
		}
		return nil
	})
}

func applyRules(p *Profile, res *Result, model string) error {
	if p.Rules == nil || p.Rules.MonoOnlyModelsRegex == "" || model == "" {
		return nil
	}
	ok, err := p.MatchString(p.Rules.MonoOnlyModelsRegex, model)
	if err != nil {
		return err
	}
	if ok {
		// Equipamento sem cor: cor = 0; se o PB não veio da fonte, PB = total.
		res.MonoOnly = true
		res.Counters["color"] = 0
		if _, has := res.Counters["mono"]; !has {
			if total, ok := res.Counters["total"]; ok {
				res.Counters["mono"] = total
			}
		}
	}
	return nil
}

func getMap(ctx context.Context, src snmp.Source, oids []string) (map[string]snmp.PDU, error) {
	out := map[string]snmp.PDU{}
	if len(oids) == 0 {
		return out, nil
	}
	unique := slices.Compact(slices.Sorted(slices.Values(oids)))
	pdus, err := src.Get(ctx, unique)
	if err != nil {
		return nil, err
	}
	for i, pdu := range pdus {
		if i < len(unique) {
			out[unique[i]] = pdu
		}
	}
	return out, nil
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
