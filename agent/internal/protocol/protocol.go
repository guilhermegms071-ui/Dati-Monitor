// Package protocol defines the JSON messages exchanged between dm-agent and the server
// (documented in docs/protocol.md; mirrored by backend/app/schemas/agent.py). Every message
// carries "v": 1.
package protocol

import (
	"encoding/json"
	"strings"
	"time"

	"github.com/daticopy/dati-monitor/agent/internal/printer"
	"github.com/daticopy/dati-monitor/agent/internal/profile"
	"github.com/daticopy/dati-monitor/agent/internal/snmp"
)

// Version of the protocol.
const Version = 1

// Item kinds sent to /api/agent/readings.
const (
	KindReading    = "reading"
	KindSupplies   = "supplies"
	KindStatus     = "status"
	KindEvent      = "event"
	KindAttributes = "attributes"
)

// Item result statuses returned by the server.
const (
	ResultAccepted  = "accepted"
	ResultDuplicate = "duplicate"
	ResultDiscarded = "discarded"
	ResultRejected  = "rejected"
)

// EnrollRequest exchanges the 8-character code for credentials (POST /api/agent/enroll).
type EnrollRequest struct {
	V        int      `json:"v"`
	Code     string   `json:"code"`
	Hostname string   `json:"hostname"`
	OS       string   `json:"os"`
	Arch     string   `json:"arch"`
	Kind     string   `json:"kind"` // windows | linux
	Version  string   `json:"version"`
	LocalIPs []string `json:"local_ips,omitempty"`
	HostMAC  string   `json:"host_mac,omitempty"`
}

// EnrollResponse returns the agent id and the 32-byte secret (base64). Only a derived key is kept
// on the server.
type EnrollResponse struct {
	V          int       `json:"v"`
	AgentID    string    `json:"agent_id"`
	Secret     string    `json:"secret"`
	ServerTime time.Time `json:"server_time"`
	WSURL      string    `json:"ws_url"`
}

// TokenRequest proves possession of the secret: signature = hex(HMAC-SHA256(K, agent_id\nts\nnonce))
// with K = SHA-256("dm-agent-auth\n" + secret).
type TokenRequest struct {
	V         int    `json:"v"`
	AgentID   string `json:"agent_id"`
	Timestamp int64  `json:"ts"`
	Nonce     string `json:"nonce"`
	Signature string `json:"signature"`
}

// TokenResponse carries a short-lived JWT (15 min).
type TokenResponse struct {
	V           int       `json:"v"`
	AccessToken string    `json:"access_token"`
	ExpiresAt   time.Time `json:"expires_at"`
	ServerTime  time.Time `json:"server_time"`
}

// HeartbeatRequest is sent every 30 s (POST /api/agent/heartbeat, or over the WebSocket).
type HeartbeatRequest struct {
	V                    int        `json:"v"`
	Ts                   time.Time  `json:"ts"`
	Version              string     `json:"version"`
	ClusterRole          string     `json:"cluster_role"`
	CPUPercent           float64    `json:"cpu_percent"`
	MemoryBytes          uint64     `json:"memory_bytes"`
	QueuePending         int        `json:"queue_pending"`
	QueueDropped         int        `json:"queue_dropped"`
	UptimeSeconds        int64      `json:"uptime_seconds"`
	LocalIPs             []string   `json:"local_ips,omitempty"`
	Hostname             string     `json:"hostname"`
	OS                   string     `json:"os"`
	Arch                 string     `json:"arch"`
	HostMAC              string     `json:"host_mac,omitempty"`
	AppliedConfigVersion int        `json:"applied_config_version"`
	LastScanAt           *time.Time `json:"last_scan_at,omitempty"`
	LastReadAt           *time.Time `json:"last_read_at,omitempty"`
	DevicesKnown         int        `json:"devices_known"`
	Paused               bool       `json:"paused"`
	Errors               []string   `json:"errors,omitempty"`
	LatencyMS            *float64   `json:"latency_ms,omitempty"`
	WSConnected          bool       `json:"ws_connected"`
	InstallPath          string     `json:"install_path,omitempty"`
	// WatchdogState is the dm-watchdog service as the agent sees it (mutual watch, PROMPT 5.1).
	WatchdogState string `json:"watchdog_state,omitempty"`
}

// HeartbeatResponse tells the agent its role and the current configuration version.
type HeartbeatResponse struct {
	V             int       `json:"v"`
	ServerTime    time.Time `json:"server_time"`
	ConfigVersion int       `json:"config_version"`
	ClusterRole   string    `json:"cluster_role"`
	Paused        bool      `json:"paused"`
}

// Intervals of the independent collection loops (PROMPT 4.6), in minutes.
type Intervals struct {
	DiscoveryMinutes  int `json:"discovery_minutes"`
	CountersMinutes   int `json:"counters_minutes"`
	SuppliesMinutes   int `json:"supplies_minutes"`
	StatusMinutes     int `json:"status_minutes"`
	AttributesMinutes int `json:"attributes_minutes"`
}

// DiscoveryConfig tunes the scanner (PROMPT 4.5).
type DiscoveryConfig struct {
	Concurrency int `json:"concurrency"`
	RatePPS     int `json:"rate_pps"`
	TimeoutMS   int `json:"timeout_ms"`
	Retries     int `json:"retries"`
	// ReadTimeoutMS is the per-query timeout of the readings (the discovery uses TimeoutMS).
	ReadTimeoutMS int `json:"read_timeout_ms"`
}

// IPRange is an approved discovery range (CIDR or start–end), with exclusions and SNMP ports.
type IPRange struct {
	ID         string   `json:"id"`
	CIDR       string   `json:"cidr,omitempty"`
	Start      string   `json:"start_ip,omitempty"`
	End        string   `json:"end_ip,omitempty"`
	Host       string   `json:"host,omitempty"` // IP or hostname of a single device (PROMPT 16.10)
	Exclusions []string `json:"exclusions,omitempty"`
	Ports      []int    `json:"ports,omitempty"`
}

// AgentConfig is the full configuration of the agent's site (GET /api/agent/config).
type AgentConfig struct {
	V             int               `json:"v"`
	ConfigVersion int               `json:"config_version"`
	SiteID        string            `json:"site_id"`
	ClusterRole   string            `json:"cluster_role"`
	Paused        bool              `json:"paused"`
	Intervals     Intervals         `json:"intervals"`
	Discovery     DiscoveryConfig   `json:"discovery"`
	Ranges        []IPRange         `json:"ranges"`
	Credentials   []snmp.Credential `json:"credentials"`
	Profiles      []json.RawMessage `json:"profiles"`
	ProxyURL      string            `json:"proxy_url,omitempty"`
	KeepAwake     bool              `json:"keep_awake"`
	WSURL         string            `json:"ws_url"`
	// MonitorLocalNetworks also scans the private /24 of this PC's interfaces (PROMPT 16.10).
	MonitorLocalNetworks bool `json:"monitor_local_networks"`
	// IgnoredSerials were discarded in Descobertas: never read them (PROMPT 16.1).
	IgnoredSerials []string `json:"ignored_serials"`
}

// SuggestRangesRequest sends the /24 of the agent's private interfaces when the site has no range.
type SuggestRangesRequest struct {
	V      int      `json:"v"`
	Ranges []string `json:"ranges"`
}

// DeviceRef identifies the device an item refers to (serial is the identity; PROMPT 4.6).
type DeviceRef struct {
	IP          string `json:"ip"`
	Port        int    `json:"port"`
	Serial      string `json:"serial"`
	MAC         string `json:"mac,omitempty"`
	Hostname    string `json:"hostname,omitempty"`
	SysObjectID string `json:"sys_object_id,omitempty"`
	SysDescr    string `json:"sys_descr,omitempty"`
	Model       string `json:"model,omitempty"`
	Firmware    string `json:"firmware,omitempty"`
	ProfileKey  string `json:"profile_key,omitempty"`
	SysLocation string `json:"sys_location,omitempty"`
}

// ReadingPayload is a counter reading. Counters holds every resolved counter; the server maps the
// normalized names to columns and keeps the rest (and Extra) in readings.extra.
type ReadingPayload struct {
	Counters map[string]int64 `json:"counters"`
	// CounterLines are the profile's explicit `line` mappings (PROMPT 16.2).
	CounterLines        map[string]profile.Line `json:"counter_lines,omitempty"`
	Extra               map[string]any          `json:"extra,omitempty"`
	CounterSource       string                  `json:"counter_source"`
	ProfileKey          string                  `json:"profile_key"`
	ProfileVersion      int                     `json:"profile_version"`
	MonoOnly            bool                    `json:"mono_only"`
	SumTolerancePercent float64                 `json:"sum_tolerance_percent"`
	Unresolved          []string                `json:"unresolved,omitempty"`
	Status              string                  `json:"status,omitempty"`
	ErrorBits           int                     `json:"error_bits"`
	Source              string                  `json:"source"` // snmp | http
	Attempts            int                     `json:"attempts"`
}

// EventPayload is a device event observed by the agent (e.g. read_failed).
type EventPayload struct {
	Type string         `json:"type"`
	Data map[string]any `json:"data,omitempty"`
}

// Item is one queued element sent in /api/agent/readings. Key = "<agent_id>:<local seq>".
type Item struct {
	Key      string                `json:"key"`
	Kind     string                `json:"kind"`
	ReadAt   time.Time             `json:"read_at"`
	Device   DeviceRef             `json:"device"`
	Reading  *ReadingPayload       `json:"reading,omitempty"`
	Supplies []printer.Supply      `json:"supplies,omitempty"`
	Status   *printer.StatusResult `json:"status,omitempty"`
	Event    *EventPayload         `json:"event,omitempty"`
	// Attributes of the daily read (PROMPT 16.8).
	Attributes *printer.Attributes `json:"attributes,omitempty"`
}

// ReadingsRequest is a batch of up to 500 items (gzip).
type ReadingsRequest struct {
	V     int    `json:"v"`
	Items []Item `json:"items"`
}

// ItemResult tells the agent what happened to each key.
type ItemResult struct {
	Key    string `json:"key"`
	Status string `json:"status"`
	Reason string `json:"reason,omitempty"`
}

// ReadingsResponse lists one result per received key.
type ReadingsResponse struct {
	V       int          `json:"v"`
	Results []ItemResult `json:"results"`
}

// ErrorBody is the server error format ({"detail": {"code", "message", ...}}).
type ErrorBody struct {
	Detail struct {
		Code       string    `json:"code"`
		Message    string    `json:"message"`
		ServerTime time.Time `json:"server_time"`
	} `json:"detail"`
}

// Command states reported by the agent (PROMPT 4.7).
const (
	StateAcked     = "acked"
	StateRunning   = "running"
	StateSucceeded = "succeeded"
	StateFailed    = "failed"
)

// CommandMessage is a command delivered to the agent (WebSocket or GET /api/agent/commands/pending).
type CommandMessage struct {
	V         int             `json:"v"`
	ID        string          `json:"id"`
	Type      string          `json:"type"`
	Params    json.RawMessage `json:"params"`
	CreatedAt time.Time       `json:"created_at"`
	ExpiresAt time.Time       `json:"expires_at"`
}

// CommandUpdate reports progress/result of a command (idempotent by ID on the server).
type CommandUpdate struct {
	V        int            `json:"v"`
	ID       string         `json:"id"`
	State    string         `json:"state"`
	Progress string         `json:"progress,omitempty"`
	Result   map[string]any `json:"result,omitempty"`
	Output   string         `json:"output,omitempty"`
	Error    string         `json:"error,omitempty"`
}

// CommandUpdateResponse is the server's view of the command after an update.
type CommandUpdateResponse struct {
	V     int    `json:"v"`
	ID    string `json:"id"`
	State string `json:"state"`
}

// PendingCommandsResponse is the answer of the HTTPS contingency channel.
type PendingCommandsResponse struct {
	V        int              `json:"v"`
	Commands []CommandMessage `json:"commands"`
}

// UploadResponse acknowledges an uploaded file (logs, walk).
type UploadResponse struct {
	V         int    `json:"v"`
	ID        string `json:"id"`
	SizeBytes int64  `json:"size_bytes"`
}

// WebSocket message types (/ws/agent).
const (
	WSHello            = "hello"
	WSHeartbeat        = "heartbeat"
	WSCommandUpdate    = "command_update"
	WSWelcome          = "welcome"
	WSHeartbeatAck     = "heartbeat_ack"
	WSCommand          = "command"
	WSCancel           = "cancel"
	WSCommandUpdateAck = "command_update_ack"
	WSError            = "error"
	// Túnel da página web da impressora (PROMPT 4.9).
	WSWebRequest  = "web_request"
	WSWebResponse = "web_response"
	WSWebChunk    = "web_chunk"
	WSWebError    = "web_error"
)

// WebPorts are the only ports the tunnel opens (PROMPT 4.9).
var WebPorts = []int{80, 443, 8000, 8080, 8443}

// WebProxyOpenParams are the parameters of the web_proxy_open command.
type WebProxyOpenParams struct {
	SessionID         string    `json:"session_id"`
	IP                string    `json:"ip"`
	Port              int       `json:"port"`
	Scheme            string    `json:"scheme"`
	ExpiresAt         time.Time `json:"expires_at"`
	MaxBytesPerSecond int       `json:"max_bytes_per_second"`
}

// WebRequest is a browser request forwarded by the gateway (body in base64).
type WebRequest struct {
	StreamID  string      `json:"stream_id"`
	SessionID string      `json:"session_id"`
	Method    string      `json:"method"`
	Path      string      `json:"path"`
	Headers   [][2]string `json:"headers"`
	BodyB64   *string     `json:"body_b64"`
}

// WebResponseStart carries the printer's status line and headers.
type WebResponseStart struct {
	V        int         `json:"v"`
	StreamID string      `json:"stream_id"`
	Status   int         `json:"status"`
	Headers  [][2]string `json:"headers,omitempty"`
}

// WebChunk is a piece of the response body (End marks the last one).
type WebChunk struct {
	V        int    `json:"v"`
	StreamID string `json:"stream_id"`
	DataB64  string `json:"data_b64"`
	End      bool   `json:"end"`
}

// WebError ends a stream that could not be served.
type WebError struct {
	V        int    `json:"v"`
	StreamID string `json:"stream_id"`
	Message  string `json:"message"`
}

// WSMessage is the envelope of every WebSocket message.
type WSMessage struct {
	V    int             `json:"v"`
	Type string          `json:"type"`
	Data json.RawMessage `json:"data,omitempty"`
}

// Hello is the first message sent by the agent after connecting.
type Hello struct {
	V            int      `json:"v"`
	Version      string   `json:"version"`
	Capabilities []string `json:"capabilities,omitempty"`
}

// Welcome is the server's greeting.
type Welcome struct {
	V                int       `json:"v"`
	AgentID          string    `json:"agent_id"`
	ServerTime       time.Time `json:"server_time"`
	HeartbeatSeconds int       `json:"heartbeat_seconds"`
}

// WSErrorData is the payload of an "error" message.
type WSErrorData struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	ID      string `json:"id,omitempty"`
}

// Service states one product service reports about the other (agent ↔ watchdog).
const (
	ServiceRunning      = "running"
	ServiceStopped      = "stopped"
	ServiceStarting     = "starting"
	ServiceNotInstalled = "not_installed"
	ServiceUnknown      = "unknown"
)

// WatchdogRestart is one agent restart done by the watchdog, with the reason.
type WatchdogRestart struct {
	At     time.Time `json:"at"`
	Reason string    `json:"reason"`
}

// WatchdogHeartbeatRequest is sent by dm-watchdog every 60 s (POST /api/watchdog/heartbeat).
type WatchdogHeartbeatRequest struct {
	V                    int               `json:"v"`
	Ts                   time.Time         `json:"ts"`
	Version              string            `json:"version"`
	OS                   string            `json:"os"`
	Arch                 string            `json:"arch"`
	AgentState           string            `json:"agent_state"`
	AgentHealthy         bool              `json:"agent_healthy"`
	AgentVersion         string            `json:"agent_version"`
	AgentMemoryBytes     uint64            `json:"agent_memory_bytes"`
	PreviousAgentVersion string            `json:"previous_agent_version"`
	Restarts             []WatchdogRestart `json:"restarts,omitempty"`
	Errors               []string          `json:"errors,omitempty"`
}

// WatchdogHeartbeatResponse carries the commands addressed to the watchdog.
type WatchdogHeartbeatResponse struct {
	V          int              `json:"v"`
	ServerTime time.Time        `json:"server_time"`
	Commands   []CommandMessage `json:"commands"`
}

// UpdateParams are the params of an `update` command (PROMPT 5.2).
type UpdateParams struct {
	ReleaseID string `json:"release_id"`
	Component string `json:"component"` // agent | watchdog
	Version   string `json:"version"`
	OS        string `json:"os"`
	Arch      string `json:"arch"`
	SHA256    string `json:"sha256"`
	Signature string `json:"signature"` // ed25519, base64
	SizeBytes int64  `json:"size_bytes"`
	URL       string `json:"url"`
}

// ReleaseMessage is the exact message covered by a release's ed25519 signature: it binds the binary's
// sha256 to its component, version and target, so an old signed binary cannot pose as a new version.
func ReleaseMessage(component, version, goos, goarch, sha256hex string) []byte {
	return []byte(strings.Join([]string{"dati-monitor-release/v1", component, version, goos, goarch, sha256hex}, "\n"))
}

// MarshalJSON sends "ranges" as a list even when nil (the server requires the field and refuses null).
func (r SuggestRangesRequest) MarshalJSON() ([]byte, error) {
	type plain SuggestRangesRequest
	if r.Ranges == nil {
		r.Ranges = []string{}
	}
	return json.Marshal(plain(r))
}

// MarshalJSON sends "counters" as an object even when nil (required by the server; null is refused).
func (p ReadingPayload) MarshalJSON() ([]byte, error) {
	type plain ReadingPayload
	if p.Counters == nil {
		p.Counters = map[string]int64{}
	}
	return json.Marshal(plain(p))
}
