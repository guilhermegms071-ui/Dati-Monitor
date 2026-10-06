package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

// SetServerSignature is the HMAC the server puts on the "Mudar endereço do servidor" command, with the key
// of this collector (backend: app.core.security.set_server_signature).
func SetServerSignature(key []byte, agentID, serverURL, wsURL string, issuedAt int64) string {
	m := hmac.New(sha256.New, key)
	_, _ = fmt.Fprintf(m, "dm-set-server\n%s\n%s\n%s\n%d", agentID, serverURL, wsURL, issuedAt)
	return hex.EncodeToString(m.Sum(nil))
}

// VerifySetServer checks the signature of a server change with this collector's key.
func (c *Client) VerifySetServer(serverURL, wsURL string, issuedAt int64, signature string) bool {
	want := SetServerSignature(c.key, c.agentID, serverURL, wsURL, issuedAt)
	return hmac.Equal([]byte(want), []byte(signature))
}

// WithServer is a new client for another server with the same identity (agent id and key): used to prove
// that the new server knows this collector before switching to it.
func (c *Client) WithServer(opts Options) (*Client, error) {
	return New(opts, c.agentID, c.key)
}
