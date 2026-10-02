import type { Schemas } from './api';

type Kind = Schemas['InstallerOut']['kind'];

/** Tipo, arquitetura e versão pelo nome do arquivo gerado pelos scripts de build (preenche o formulário). */
export function guessInstaller(name: string): { kind: Kind; arch: string; version: string } {
  // O .tar.gz termina em "-linux-<arch>.tar.gz": fora disso, o "-linux" pareceria sufixo da versão.
  const base = name.replace(/-linux-[^-]+\.tar\.gz$/, '');
  const version = /(\d+\.\d+\.\d+(?:[-~][0-9A-Za-z.]+)?)/.exec(base)?.[1]?.replace('~', '-') ?? '';
  if (name.endsWith('.exe')) return { kind: 'windows', arch: 'all', version };
  let arch = 'amd64';
  if (/armhf|linux-arm(?!64)/.test(name)) arch = 'arm';
  else if (/arm64/.test(name)) arch = 'arm64';
  else if (/i386|-386/.test(name)) arch = '386';
  return { kind: name.endsWith('.deb') ? 'deb' : 'tar', arch, version };
}
