//go:build !windows

package api

// systemProxy: on Linux the proxy comes from the environment (HTTPS_PROXY/NO_PROXY) or the config.
func systemProxy() *sysProxy { return nil }
