package collector

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/usbprint"
)

// HealthUSB is the health loop name of the USB printer inventory.
const HealthUSB = "usb"

// USBDeps lets tests replace the Windows-only parts.
type USBDeps struct {
	List      func(context.Context) ([]usbprint.Printer, error)
	PageCount func(context.Context, usbprint.Printer) (int64, string, error)
	Hostname  func() string
}

func (d USBDeps) withDefaults() USBDeps {
	if d.List == nil {
		d.List = usbprint.List
	}
	if d.PageCount == nil {
		d.PageCount = usbprint.PageCount
	}
	if d.Hostname == nil {
		d.Hostname = osinfo.Hostname
	}
	return d
}

// RunUSB reports this PC's USB printers (PROMPT 11), on every PC with a collector — not only the MASTER:
// a USB printer is seen only by the PC it is connected to. Each cycle sends the status of every printer
// and, when it answers PJL, a reading with the page counter (source=usb). Printers that do not answer
// stay "sem contador disponível" and accept manual readings in the portal.
func (c *Collector) RunUSB(ctx context.Context, d USBDeps) {
	d = d.withDefaults()
	c.d.Health.Register(HealthUSB, 2*time.Hour)
	for {
		c.d.Health.Beat(HealthUSB)
		c.mu.Lock()
		paused, every := c.paused, time.Duration(c.intervals.CountersMinutes)*time.Minute
		c.mu.Unlock()
		if every <= 0 {
			every = time.Hour
		}
		if !paused {
			c.ScanUSB(ctx, d)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(every):
		}
	}
}

// ScanUSB runs one USB inventory cycle; returns how many printers were reported.
func (c *Collector) ScanUSB(ctx context.Context, d USBDeps) int {
	results, err := c.scanUSB(ctx, d, nil)
	if err != nil {
		return 0
	}
	return len(results)
}

// ReadUSBNow reads this PC's USB printers now ("Ler agora"), on any collector — MASTER or STANDBY, since
// only this PC sees them. With serials, only those printers; the result says which ones had a counter.
func (c *Collector) ReadUSBNow(ctx context.Context, d USBDeps, serials []string) ([]DeviceReadResult, error) {
	var only map[string]bool
	if len(serials) > 0 {
		only = map[string]bool{}
		for _, s := range serials {
			only[s] = true
		}
	}
	return c.scanUSB(ctx, d, only)
}

// scanUSB reports the USB printers (all, or the serials in `only`) and returns the outcome of each one.
func (c *Collector) scanUSB(ctx context.Context, d USBDeps, only map[string]bool) ([]DeviceReadResult, error) {
	d = d.withDefaults()
	printers, err := d.List(ctx)
	if err != nil {
		c.d.Log.Error("inventário de impressoras USB falhou", "erro", err)
		return nil, fmt.Errorf("inventário de impressoras USB: %w", err)
	}
	host := d.Hostname()
	var results []DeviceReadResult
	for _, p := range printers {
		ref := protocol.DeviceRef{
			Serial: p.Serial(host), Model: p.Model(), Brand: p.Brand(), Hostname: host, Source: "usb",
			SysDescr: p.Name + " (" + p.Port + ")",
		}
		if only != nil && !only[ref.Serial] {
			continue
		}
		res := DeviceReadResult{IP: "USB " + p.Port, Serial: ref.Serial}
		st := printer.StatusResult{Status: "ready"}
		if p.Offline {
			st.Status = "offline"
		}
		c.enqueue(ctx, protocol.Item{Kind: protocol.KindStatus, Device: ref, Status: &st})
		if p.Offline {
			res.Error = "impressora USB desligada ou desconectada"
			results = append(results, res)
			continue
		}
		count, model, err := d.PageCount(ctx, p)
		if err != nil {
			level := c.d.Log.Info
			if !errors.Is(err, usbprint.ErrNoAnswer) {
				level = c.d.Log.Warn
			}
			level("impressora USB sem contador disponível", "impressora", p.Name, "porta", p.Port, "motivo", err)
			res.Error = "sem contador disponível (use a leitura manual no portal)"
			results = append(results, res)
			continue
		}
		if model != "" {
			ref.Model = model
		}
		c.enqueue(ctx, protocol.Item{Kind: protocol.KindReading, Device: ref, Reading: &protocol.ReadingPayload{
			Counters: map[string]int64{"total": count}, CounterSource: "pjl", Source: "usb", Status: st.Status,
		}})
		res.OK = true
		results = append(results, res)
	}
	if len(printers) > 0 {
		c.d.Log.Info("impressoras USB reportadas", "quantidade", len(printers))
	}
	return results, nil
}
