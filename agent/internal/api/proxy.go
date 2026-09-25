package api

import (
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strings"
)

// proxyFunc selects the HTTP proxy (PROMPT 4.3): manual URL from the configuration first, then the
// standard environment variables (HTTPS_PROXY/NO_PROXY), then the system proxy (WinHTTP on Windows).
func proxyFunc(manual string) (func(*http.Request) (*url.URL, error), error) {
	if manual != "" {
		u, err := url.Parse(manual)
		if err != nil || u.Host == "" {
			return nil, fmt.Errorf("proxy inválido: %q", manual)
		}
		return http.ProxyURL(u), nil
	}
	system := systemProxy()
	return func(r *http.Request) (*url.URL, error) {
		if u, err := http.ProxyFromEnvironment(r); u != nil || err != nil {
			return u, err
		}
		if system == nil || bypass(r.URL.Hostname(), system.bypass) {
			return nil, nil
		}
		return system.proxyFor(r.URL.Scheme), nil
	}, nil
}

type sysProxy struct {
	byScheme map[string]*url.URL // "http", "https" ou "" (todos)
	bypass   []string
}

func (s *sysProxy) proxyFor(scheme string) *url.URL {
	if u, ok := s.byScheme[scheme]; ok {
		return u
	}
	return s.byScheme[""]
}

// parseProxyList parses Windows-style proxy strings: "host:port" or "http=h1:p;https=h2:p".
func parseProxyList(server, bypassList string) *sysProxy {
	server = strings.TrimSpace(server)
	if server == "" {
		return nil
	}
	sp := &sysProxy{byScheme: map[string]*url.URL{}}
	for _, part := range strings.FieldsFunc(server, func(r rune) bool { return r == ';' || r == ' ' }) {
		scheme := ""
		if k, v, ok := strings.Cut(part, "="); ok {
			scheme, part = strings.ToLower(k), v
		}
		if !strings.Contains(part, "://") {
			part = "http://" + part
		}
		if u, err := url.Parse(part); err == nil && u.Host != "" {
			sp.byScheme[scheme] = u
		}
	}
	if len(sp.byScheme) == 0 {
		return nil
	}
	for _, b := range strings.FieldsFunc(bypassList, func(r rune) bool { return r == ';' || r == ' ' }) {
		sp.bypass = append(sp.bypass, strings.ToLower(strings.TrimSpace(b)))
	}
	return sp
}

// bypass implements the WinHTTP bypass list: "<local>" (names without dots), exact hosts and
// wildcards like "*.empresa.local" or "10.*".
func bypass(host string, list []string) bool {
	host = strings.ToLower(host)
	for _, b := range list {
		switch {
		case b == "<local>":
			if !strings.Contains(host, ".") || net.ParseIP(host) != nil && net.ParseIP(host).IsLoopback() {
				return true
			}
		case strings.HasPrefix(b, "*"):
			if strings.HasSuffix(host, strings.TrimPrefix(b, "*")) {
				return true
			}
		case strings.HasSuffix(b, "*"):
			if strings.HasPrefix(host, strings.TrimSuffix(b, "*")) {
				return true
			}
		case host == b:
			return true
		}
	}
	return false
}
