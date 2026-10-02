package agent

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"path/filepath"
	"runtime"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/buildinfo"
	"github.com/daticopy/dati-monitor/agent/internal/collector"
	"github.com/daticopy/dati-monitor/agent/internal/commands"
	"github.com/daticopy/dati-monitor/agent/internal/logx"
	"github.com/daticopy/dati-monitor/agent/internal/netdiag"
	"github.com/daticopy/dati-monitor/agent/internal/osinfo"
	"github.com/daticopy/dati-monitor/agent/internal/product"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/protocol"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
	"github.com/daticopy/dati-monitor/agent/internal/svc"
)

// Limits of the upload commands.
const (
	MaxLogZip        = 45 << 20
	WalkTimeout      = 30 * time.Minute
	ReconnectTimeout = 30 * time.Second
)

func decode[T any](cmd protocol.CommandMessage) (T, error) {
	var v T
	if len(cmd.Params) == 0 || string(cmd.Params) == "null" {
		return v, nil
	}
	if err := json.Unmarshal(cmd.Params, &v); err != nil {
		return v, fmt.Errorf("parâmetros inválidos: %w", err)
	}
	return v, nil
}

type target struct {
	IP   string `json:"ip"`
	Port int    `json:"port"`
}

func (t target) check() (target, error) {
	if net.ParseIP(t.IP).To4() == nil {
		return t, fmt.Errorf("IP inválido: %q", t.IP)
	}
	if t.Port == 0 {
		t.Port = 161
	}
	return t, nil
}

// commandSpecs maps every command type this build executes (PROMPT 4.7). The watchdog runs its own:
// restart_agent, rollback, uninstall and the agent's update; the agent runs the watchdog's update.
func (a *Agent) commandSpecs() map[string]commands.Spec {
	return map[string]commands.Spec{
		"reconnect":        {Handler: a.cmdReconnect},
		"restart_watchdog": {Handler: a.cmdRestartWatchdog},
		"scan_now":         {Handler: a.cmdScanNow, Timeout: 30 * time.Minute},
		"read_now":         {Handler: a.cmdReadNow, Timeout: 30 * time.Minute},
		"read_device":      {Handler: a.cmdReadDevice, Timeout: 5 * time.Minute},
		"snmp_test":        {Handler: a.cmdSNMPTest, Timeout: 5 * time.Minute},
		"mib_walk":         {Handler: a.cmdMibWalk, Timeout: WalkTimeout},
		"set_config":       {Handler: a.cmdSetConfig},
		"get_logs":         {Handler: a.cmdGetLogs},
		"diagnostics":      {Handler: a.cmdDiagnostics, Timeout: 2 * time.Minute},
		"pause":            {Handler: a.cmdPause(true)},
		"resume":           {Handler: a.cmdPause(false)},
		"promote_master":   {Handler: a.cmdPromote},
		"wake_host":        {Handler: a.cmdWakeHost},
		"ping_host":        {Handler: a.cmdPingHost, Timeout: 2 * time.Minute},
		"update":           {Handler: a.cmdUpdate, Timeout: 10 * time.Minute}, // só do watchdog (processo inverso)
		"web_proxy_open":   {Handler: a.cmdWebProxyOpen},
	}
}

func (a *Agent) cmdReconnect(ctx context.Context, _ protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	a.Client.ResetToken()
	progress("fechando o canal WebSocket e renovando o token")
	a.WS.Reconnect()
	a.Uploader.Kick() // reenvia a fila
	wait := a.ReconnectWait
	if wait <= 0 {
		wait = ReconnectTimeout
	}
	deadline := time.Now().Add(wait)
	time.Sleep(200 * time.Millisecond) // dá tempo de o canal antigo cair
	for !a.WS.Connected() {
		if time.Now().After(deadline) {
			return commands.Result{}, fmt.Errorf("o canal WebSocket não voltou em %s (a fila segue pelo HTTPS)", wait)
		}
		select {
		case <-ctx.Done():
			return commands.Result{}, ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
	pending, _ := a.Store.Count(ctx)
	return commands.Result{Data: map[string]any{"reconnected": true, "queue_pending": pending}}, nil
}

// cmdWebProxyOpen registers a printer web page session (PROMPT 4.9); requests come by web_request.
func (a *Agent) cmdWebProxyOpen(_ context.Context, cmd protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	p, err := decode[protocol.WebProxyOpenParams](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	if err := a.Web.Open(p); err != nil {
		return commands.Result{}, err
	}
	return commands.Result{Data: map[string]any{"session_id": p.SessionID, "expires_at": p.ExpiresAt}}, nil
}

func (a *Agent) cmdRestartWatchdog(_ context.Context, _ protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	name := product.WatchdogServiceName()
	if err := svc.RestartService(name); err != nil {
		return commands.Result{}, err
	}
	state, _ := svc.ServiceState(name)
	return commands.Result{Data: map[string]any{"service": name, "state": state}}, nil
}

func (a *Agent) cmdScanNow(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	p, err := decode[struct {
		RangeID string `json:"range_id"`
	}](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	progress("varrendo as faixas de IP")
	res, err := a.Collector.ScanNow(ctx, p.RangeID)
	if err != nil {
		return commands.Result{}, err
	}
	return commands.Result{Data: toMap(res)}, nil
}

func (a *Agent) cmdReadNow(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	p, err := decode[struct {
		Devices []collector.Selector `json:"devices"`
	}](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	progress("lendo os equipamentos")
	sum, err := a.Collector.ReadNow(ctx, p.Devices)
	if err != nil {
		return commands.Result{}, err
	}
	a.Uploader.Kick()
	return commands.Result{Data: toMap(sum)}, nil
}

// readDeviceParams: `profile` is a draft being tested in the portal (Perfis de modelos, PROMPT 6.6).
type readDeviceParams struct {
	target
	Profile json.RawMessage `json:"profile,omitempty"`
}

func (a *Agent) cmdReadDevice(ctx context.Context, cmd protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	params, err := decode[readDeviceParams](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	t, err := params.check()
	if err != nil {
		return commands.Result{}, err
	}
	var draft *profile.Profile
	if len(params.Profile) > 0 && string(params.Profile) != "null" {
		if draft, err = profile.FromJSON(params.Profile); err != nil {
			return commands.Result{}, fmt.Errorf("perfil em teste inválido: %w", err)
		}
	}
	raw, err := a.Collector.ReadRaw(ctx, t.IP, t.Port, draft)
	if err != nil {
		return commands.Result{}, err
	}
	return commands.Result{Data: toMap(raw)}, nil
}

func (a *Agent) cmdSNMPTest(ctx context.Context, cmd protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	t, err := decode[target](cmd)
	if err == nil {
		t, err = t.check()
	}
	if err != nil {
		return commands.Result{}, err
	}
	res, err := a.Collector.SNMPTest(ctx, t.IP, t.Port)
	if err != nil {
		return commands.Result{}, err
	}
	answered := 0
	for _, r := range res {
		if r.OK {
			answered++
		}
	}
	return commands.Result{Data: map[string]any{"ip": t.IP, "port": t.Port, "answered": answered, "results": toAny(res)}}, nil
}

func (a *Agent) cmdMibWalk(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	p, err := decode[struct {
		target
		RootOID string `json:"root_oid"`
	}](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	t, err := p.check()
	if err != nil {
		return commands.Result{}, err
	}
	if p.RootOID != "" && !snmp.ValidOID(p.RootOID) {
		return commands.Result{}, fmt.Errorf("OID inválido: %q", p.RootOID)
	}
	progress("percorrendo a MIB da impressora")
	pdus, credID, err := a.Collector.Walk(ctx, t.IP, t.Port, p.RootOID, func(n int) {
		progress(fmt.Sprintf("%d OIDs lidos", n))
	})
	if err != nil {
		return commands.Result{}, err
	}
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if err := snmp.WriteSnmprec(zw, pdus); err != nil {
		return commands.Result{}, err
	}
	if err := zw.Close(); err != nil {
		return commands.Result{}, err
	}
	progress(fmt.Sprintf("enviando %d OIDs ao servidor", len(pdus)))
	up, err := a.Client.Upload(ctx, "mib-walk", cmd.ID, "application/gzip", buf.Bytes())
	if err != nil {
		return commands.Result{}, fmt.Errorf("enviar walk: %w", err)
	}
	return commands.Result{Data: map[string]any{
		"walk_id": up.ID, "oids": len(pdus), "bytes": buf.Len(), "credential_id": credID, "ip": t.IP, "port": t.Port,
	}}, nil
}

func (a *Agent) cmdSetConfig(ctx context.Context, _ protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	cfg, err := a.Client.Config(ctx)
	if err != nil {
		return commands.Result{}, fmt.Errorf("baixar configuração: %w", err)
	}
	if err := a.apply(cfg, true); err != nil {
		return commands.Result{}, err
	}
	return commands.Result{Data: map[string]any{
		"applied_config_version": cfg.ConfigVersion, "ranges": len(cfg.Ranges), "credentials": len(cfg.Credentials),
		"profiles": len(cfg.Profiles),
	}}, nil
}

func (a *Agent) cmdGetLogs(ctx context.Context, cmd protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	p, err := decode[struct {
		Hours int `json:"hours"`
	}](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	if p.Hours <= 0 {
		p.Hours = 24
	}
	var buf bytes.Buffer
	files, err := logx.ZipRecent(filepath.Join(a.Dir, "logs"), time.Now().Add(-time.Duration(p.Hours)*time.Hour), MaxLogZip, &buf)
	if err != nil {
		return commands.Result{}, err
	}
	if files == 0 {
		return commands.Result{}, fmt.Errorf("nenhum arquivo de log modificado nas últimas %d h", p.Hours)
	}
	progress(fmt.Sprintf("enviando %d arquivo(s) de log", files))
	up, err := a.Client.Upload(ctx, "logs", cmd.ID, "application/zip", buf.Bytes())
	if err != nil {
		return commands.Result{}, fmt.Errorf("enviar logs: %w", err)
	}
	return commands.Result{Data: map[string]any{"log_id": up.ID, "files": files, "bytes": buf.Len(), "hours": p.Hours}}, nil
}

func (a *Agent) cmdPause(paused bool) commands.Handler {
	return func(_ context.Context, _ protocol.CommandMessage, _ func(string)) (commands.Result, error) {
		a.Collector.SetRole(a.Collector.Role(), paused)
		return commands.Result{Data: map[string]any{"paused": paused, "cluster_role": a.Collector.Role()}}, nil
	}
}

func (a *Agent) cmdPromote(_ context.Context, _ protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	a.Collector.SetRole("master", a.Collector.Paused())
	return commands.Result{Data: map[string]any{"cluster_role": "master"}}, nil
}

func (a *Agent) cmdWakeHost(_ context.Context, cmd protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	p, err := decode[struct {
		MAC       string   `json:"mac"`
		TargetIPs []string `json:"target_ips"`
	}](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	sent, err := netdiag.WakeOnLAN(p.MAC, p.TargetIPs)
	if err != nil {
		return commands.Result{}, err
	}
	return commands.Result{Data: map[string]any{"mac": p.MAC, "sent_to": sent}}, nil
}

func (a *Agent) cmdPingHost(ctx context.Context, cmd protocol.CommandMessage, _ func(string)) (commands.Result, error) {
	p, err := decode[struct {
		IP    string `json:"ip"`
		Ports []int  `json:"ports"`
	}](cmd)
	if err != nil {
		return commands.Result{}, err
	}
	if net.ParseIP(p.IP).To4() == nil {
		return commands.Result{}, fmt.Errorf("IP inválido: %q", p.IP)
	}
	icmp := netdiag.ICMP(ctx, p.IP, 4, 2*time.Second)
	ports := netdiag.TCPPorts(ctx, p.IP, p.Ports, 3*time.Second)
	reachable := icmp.Received > 0
	for _, r := range ports {
		reachable = reachable || r.Open || r.Refused
	}
	return commands.Result{Data: map[string]any{"ip": p.IP, "reachable": reachable, "icmp": toAny(icmp), "tcp": toAny(ports)}}, nil
}

func (a *Agent) cmdDiagnostics(ctx context.Context, _ protocol.CommandMessage, progress func(string)) (commands.Result, error) {
	out := map[string]any{
		"version": buildinfo.Version, "os": osinfo.Describe(), "arch": runtime.GOARCH, "hostname": osinfo.Hostname(),
		"server_url": a.Local.ServerURL, "ws_url": a.wsURL(), "cluster_role": a.Collector.Role(),
		"paused": a.Collector.Paused(), "uptime_s": int64(time.Since(a.started).Seconds()),
		"interfaces": toAny(netdiag.Interfaces()), "local_ips": osinfo.LocalIPv4(),
	}
	u, err := url.Parse(a.Local.ServerURL)
	if err != nil {
		return commands.Result{}, err
	}
	progress("resolvendo o nome do servidor (DNS)")
	if net.ParseIP(u.Hostname()) == nil {
		start := time.Now()
		addrs, err := net.DefaultResolver.LookupHost(ctx, u.Hostname())
		dns := map[string]any{"host": u.Hostname(), "addrs": addrs, "elapsed_ms": time.Since(start).Milliseconds()}
		if err != nil {
			dns["error"] = err.Error()
		}
		out["dns"] = dns
	} else {
		out["dns"] = map[string]any{"host": u.Hostname(), "note": "endereço IP (sem DNS)"}
	}
	progress("testando HTTPS e o proxy")
	out["https"] = a.probeHTTPS(ctx)
	out["proxy"] = a.detectedProxy()
	rtt := a.WS.RTT()
	out["websocket"] = map[string]any{
		"connected": a.WS.Connected(), "down_for_s": a.WS.DownFor().Seconds(), "rtt_ms": rtt,
		"last_error": a.wsError(),
	}
	offset := a.Client.ClockOffset()
	out["clock"] = map[string]any{
		"local_utc": time.Now().UTC(), "server_utc": a.Client.ServerNow().UTC(), "offset_s": offset.Seconds(),
		"ok": offset < 2*time.Minute && offset > -2*time.Minute,
	}
	if d, err := netdiag.DiskUsage(a.Dir); err != nil {
		out["disk"] = map[string]any{"path": a.Dir, "error": err.Error()}
	} else {
		out["disk"] = toAny(d)
	}
	pending, _ := a.Store.Count(ctx)
	dead, _ := a.Store.DeadCount(ctx)
	out["queue"] = map[string]any{"pending": pending, "dead": dead, "dropped": a.Uploader.Dropped()}
	return commands.Result{Data: out}, nil
}

func (a *Agent) probeHTTPS(ctx context.Context) map[string]any {
	target := a.Client.BaseURL()
	target.Path = "/api/health"
	res := map[string]any{"url": target.String()}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target.String(), nil)
	if err != nil {
		res["error"] = err.Error()
		return res
	}
	start := time.Now()
	resp, err := a.Client.HTTPClient().Do(req)
	res["elapsed_ms"] = time.Since(start).Milliseconds()
	if err != nil {
		res["error"] = err.Error()
		return res
	}
	_ = resp.Body.Close()
	res["status"] = resp.StatusCode
	if resp.TLS != nil {
		res["tls_version"] = resp.TLS.Version
	}
	return res
}

func (a *Agent) detectedProxy() string {
	tr, ok := a.Client.HTTPClient().Transport.(*http.Transport)
	if !ok || tr.Proxy == nil {
		return "direto"
	}
	req, err := http.NewRequest(http.MethodGet, a.Client.BaseURL().String(), nil) //nolint:noctx // só inspeciona o proxy
	if err != nil {
		return "direto"
	}
	u, err := tr.Proxy(req)
	if err != nil {
		return "erro: " + err.Error()
	}
	if u == nil {
		return "direto"
	}
	return u.Redacted()
}

// toMap / toAny turn typed results into JSON-shaped values (what the server stores).
func toMap(v any) map[string]any {
	raw, err := json.Marshal(v)
	if err != nil {
		return map[string]any{"error": err.Error()}
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return map[string]any{"value": string(raw)}
	}
	return m
}

func toAny(v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		return err.Error()
	}
	var out any
	_ = json.Unmarshal(raw, &out)
	return out
}
