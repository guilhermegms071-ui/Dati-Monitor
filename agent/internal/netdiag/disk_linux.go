//go:build linux

package netdiag

import "golang.org/x/sys/unix"

func diskUsage(path string) (total, free uint64, err error) {
	var st unix.Statfs_t
	if err := unix.Statfs(path, &st); err != nil {
		return 0, 0, err
	}
	bs := uint64(st.Bsize) //nolint:gosec // G115: tamanho de bloco é positivo
	return st.Blocks * bs, st.Bavail * bs, nil
}
