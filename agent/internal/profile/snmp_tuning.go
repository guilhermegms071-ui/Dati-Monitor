package profile

import (
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// SNMPValues are the transport settings a profile (or one of its models) may override. Nil = keep.
type SNMPValues struct {
	Retries           *int `json:"retries,omitempty"`
	TimeoutMS         *int `json:"timeout_ms,omitempty"`
	MaxRepetitions    *int `json:"max_repetitions,omitempty"`
	MaxOIDsPerGet     *int `json:"max_oids_per_get,omitempty"`
	RequestIntervalMS *int `json:"request_interval_ms,omitempty"`
}

// SNMPModel overrides the profile values for the models matching ModelRegex.
type SNMPModel struct {
	ModelRegex string `json:"model_regex"`
	Note       string `json:"note,omitempty"`
	SNMPValues
}

// SNMPTuning is the "snmp" block of a profile: pace and tolerance of the SNMP requests to its printers.
type SNMPTuning struct {
	SNMPValues
	Note   string      `json:"note,omitempty"`
	Models []SNMPModel `json:"models,omitempty"`
}

// TuneSNMP applies the profile values and then those of the first model entry whose regex matches the
// model over base (the collector settings). Without an "snmp" block it returns base unchanged.
func (p *Profile) TuneSNMP(base snmp.Options, model string) snmp.Options {
	if p == nil || p.SNMP == nil {
		return base
	}
	out := p.SNMP.apply(base)
	for _, m := range p.SNMP.Models {
		if ok, err := p.MatchString(m.ModelRegex, model); err == nil && ok {
			return m.apply(out)
		}
	}
	return out
}

func (v SNMPValues) apply(o snmp.Options) snmp.Options {
	if v.Retries != nil {
		o.Retries = *v.Retries
	}
	if v.TimeoutMS != nil {
		o.Timeout = time.Duration(*v.TimeoutMS) * time.Millisecond
	}
	if v.MaxRepetitions != nil {
		o.MaxRepetitions = uint32(*v.MaxRepetitions) //nolint:gosec // G115: 1..50 pelo schema
	}
	if v.MaxOIDsPerGet != nil {
		o.MaxOIDsPerGet = *v.MaxOIDsPerGet
	}
	if v.RequestIntervalMS != nil {
		o.RequestInterval = time.Duration(*v.RequestIntervalMS) * time.Millisecond
	}
	return o
}
