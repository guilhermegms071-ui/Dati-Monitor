//go:build !windows && !linux

package netdiag

import "errors"

func diskUsage(string) (total, free uint64, err error) {
	return 0, 0, errors.New("uso de disco não suportado neste sistema")
}
