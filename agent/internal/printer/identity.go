package printer

import (
	"context"
	"strings"

	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// Probe is the result of the discovery GET (PROMPT 4.5).
type Probe struct {
	SysObjectID string
	DeviceType  string
	Serial      string
	LifeCount   *int64
}

// IsPrinter implements the rule of PROMPT 4.5: hrDeviceType.1 == printer, or a serial /
// prtMarkerLifeCount exists.
func (p Probe) IsPrinter() bool {
	return p.DeviceType == PrinterDeviceType || p.Serial != "" || p.LifeCount != nil
}

// ReadProbe runs the discovery GET.
func ReadProbe(ctx context.Context, src snmp.Source) (Probe, error) {
	vals, err := src.Get(ctx, []string{OIDSysObjectID, OIDHrDeviceType, OIDSerial, OIDLifeCount})
	if err != nil {
		return Probe{}, err
	}
	pr := Probe{SysObjectID: vals[0].Text, DeviceType: vals[1].Text, Serial: strings.TrimSpace(vals[2].String())}
	if n, ok := vals[3].Number(); ok && vals[3].Exists() {
		pr.LifeCount = &n
	}
	return pr, nil
}

// Identity is everything the server needs to identify and describe the device.
type Identity struct {
	Serial      string `json:"serial"`
	Model       string `json:"model,omitempty"`
	Firmware    string `json:"firmware,omitempty"`
	SysObjectID string `json:"sys_object_id,omitempty"`
	SysDescr    string `json:"sys_descr,omitempty"`
	SysName     string `json:"hostname,omitempty"`
	SysLocation string `json:"sys_location,omitempty"`
	MAC         string `json:"mac,omitempty"`
	ProfileKey  string `json:"profile_key,omitempty"`
}

// ReadIdentity reads the identity using the selected profile (serial/model/firmware in order of
// preference) plus the standard system group and the first non-zero interface MAC.
func ReadIdentity(ctx context.Context, src snmp.Source, profiles []*profile.Profile) (Identity, *profile.Profile, error) {
	vals, err := src.Get(ctx, []string{OIDSysObjectID, OIDSysDescr, OIDSysName, OIDHrDeviceDescr, OIDSysLocation})
	if err != nil {
		return Identity{}, nil, err
	}
	id := Identity{
		SysObjectID: vals[0].Text,
		SysDescr:    strings.TrimSpace(vals[1].String()),
		SysName:     strings.TrimSpace(vals[2].String()),
		SysLocation: strings.TrimSpace(vals[4].String()),
	}
	// 1ª seleção só pelo sysObjectID; depois refina com o modelo lido pelo perfil.
	p := profile.Select(profiles, id.SysObjectID, strings.TrimSpace(vals[3].String()))
	pid, err := profile.ResolveIdentity(ctx, src, p)
	if err != nil {
		return Identity{}, nil, err
	}
	if refined := profile.Select(profiles, id.SysObjectID, pid.Model); refined != nil && refined != p {
		p = refined
		if pid, err = profile.ResolveIdentity(ctx, src, p); err != nil {
			return Identity{}, nil, err
		}
	}
	id.Serial, id.Model, id.Firmware = pid.Serial, pid.Model, pid.Firmware
	if p != nil {
		id.ProfileKey = p.ID
	}
	err = src.Walk(ctx, OIDIfPhysAddress, func(pdu snmp.PDU) error {
		if id.MAC == "" && pdu.Kind == snmp.KindOctetString {
			id.MAC = snmp.FormatMAC(pdu.Bytes)
		}
		return nil
	})
	if err != nil {
		return Identity{}, nil, err
	}
	return id, p, nil
}
