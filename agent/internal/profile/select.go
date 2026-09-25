package profile

import (
	"sort"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// GenericID is the always-present fallback profile (standard OIDs only).
const GenericID = "generic"

// Select returns the most specific profile for a device: the longest matching sysObjectID prefix
// wins, and a matching model_regex beats no regex; a non-matching model_regex disqualifies the
// profile. Falls back to "generic". Returns nil only when no profile at all is available.
func Select(profiles []*Profile, sysObjectID, model string) *Profile {
	type cand struct {
		p     *Profile
		score int
	}
	var cands []cand
	var generic *Profile
	for _, p := range profiles {
		if p.ID == GenericID {
			generic = p
			continue
		}
		if p.Match == nil {
			continue
		}
		score := 0
		prefix := p.Match.SysObjectIDPrefix
		if prefix != "" {
			if !usable(prefix) || !snmp.HasPrefix(sysObjectID, prefix) {
				continue
			}
			score += 10 * (strings.Count(snmp.NormalizeOID(prefix), ".") + 1)
		}
		if p.Match.ModelRegex != "" {
			ok, err := p.MatchString(p.Match.ModelRegex, model)
			if err != nil || !ok {
				continue
			}
			score += 5
		}
		if score == 0 {
			continue
		}
		cands = append(cands, cand{p, score})
	}
	if len(cands) == 0 {
		return generic
	}
	sort.SliceStable(cands, func(i, j int) bool {
		if cands[i].score != cands[j].score {
			return cands[i].score > cands[j].score
		}
		if cands[i].p.Version != cands[j].p.Version {
			return cands[i].p.Version > cands[j].p.Version
		}
		return cands[i].p.ID < cands[j].p.ID
	})
	return cands[0].p
}
