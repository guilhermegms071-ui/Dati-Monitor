//go:build !windows

package secret

// On Linux the secret file is protected by permissions (0600, owned by the service user).
func protect(data []byte) ([]byte, error)   { return append([]byte(nil), data...), nil }
func unprotect(data []byte) ([]byte, error) { return append([]byte(nil), data...), nil }
