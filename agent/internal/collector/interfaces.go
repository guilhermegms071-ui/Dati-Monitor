package collector

import (
	"encoding/json"
	"net/netip"
	"regexp"
	"sort"

	"github.com/daticopy/dati-monitor/agent/internal/discovery"
	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/store"
)

// candidate is a printer found by the scan, already identified, before it is registered.
type candidate struct {
	found   discovery.Found
	id      printer.Identity
	profile *profile.Profile
	primary string // secondaries: the interface that reads this equipment
}

// printController matches the system description of external print controllers, which answer SNMP with
// the serial of the printer they drive but count only the jobs that pass through them (no copies, no
// mono/colour split): EFI Fiery (Konica "IC-6xx", Xerox "EX-i", Canon "imagePRESS Server"), Creo, etc.
var printController = regexp.MustCompile(`(?i)\b(fiery|efi|creo|ic-\d{3}[a-z]?|ex-?i?\s?\d|imagepress server|print\s?server|(color|colour|image|print)\s?controller)\b`)

func isPrintController(id printer.Identity) bool {
	return printController.MatchString(id.SysDescr) || printController.MatchString(id.Model)
}

// splitInterfaces keeps one interface per serial. The printer's own network card wins over a print
// controller; between equals, the lowest IP (stable across scans). A controller whose serial is already
// known at a printer interface outside this scan is also secondary.
func splitInterfaces(cands []candidate, known []store.Device) (primaries, secondaries []candidate) {
	bySerial := map[string][]candidate{}
	var order []string
	for _, c := range cands {
		if _, ok := bySerial[c.id.Serial]; !ok {
			order = append(order, c.id.Serial)
		}
		bySerial[c.id.Serial] = append(bySerial[c.id.Serial], c)
	}
	knownPrinter := map[string]string{} // serial → ip:port of a known non-controller interface
	for _, d := range known {
		var id printer.Identity
		if json.Unmarshal([]byte(d.Identity), &id) == nil && d.Serial != "" && !isPrintController(id) {
			knownPrinter[d.Serial] = key(d.IP, d.Port)
		}
	}
	for _, serial := range order {
		group := bySerial[serial]
		sort.SliceStable(group, func(i, j int) bool { return better(group[i], group[j]) })
		best := group[0]
		if isPrintController(best.id) {
			if at, ok := knownPrinter[serial]; ok && at != key(best.found.Target.IP, best.found.Target.Port) {
				for _, s := range group {
					s.primary = at
					secondaries = append(secondaries, s)
				}
				continue
			}
		}
		primaries = append(primaries, best)
		for _, s := range group[1:] {
			s.primary = key(best.found.Target.IP, best.found.Target.Port)
			secondaries = append(secondaries, s)
		}
	}
	return primaries, secondaries
}

// better orders interfaces of the same equipment: printer card before controller, then lowest IP/port.
func better(a, b candidate) bool {
	if ca, cb := isPrintController(a.id), isPrintController(b.id); ca != cb {
		return !ca
	}
	ia, errA := netip.ParseAddr(a.found.Target.IP)
	ib, errB := netip.ParseAddr(b.found.Target.IP)
	if errA == nil && errB == nil && ia != ib {
		return ia.Less(ib)
	}
	if a.found.Target.IP != b.found.Target.IP {
		return a.found.Target.IP < b.found.Target.IP
	}
	return a.found.Target.Port < b.found.Target.Port
}
