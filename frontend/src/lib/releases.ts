/** Publicação de versões assinadas (seção 5.2): leitura da saída do `dm-tool sign` e conferência do arquivo. */

export interface SignedRelease {
  component: 'agent' | 'watchdog';
  version: string;
  os: 'windows' | 'linux';
  arch: 'amd64' | '386' | 'arm64' | 'arm';
  sha256: string;
  signature: string;
  size_bytes: number;
}

const TARGETS: Record<string, readonly string[]> = {
  windows: ['amd64', '386', 'arm64'],
  linux: ['amd64', '386', 'arm64', 'arm'],
};

/** Lê o JSON impresso por `dm-tool sign` (colado no portal). Erro em português se algo não confere. */
export function parseSignOutput(text: string): SignedRelease {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error('Cole exatamente o JSON impresso pelo dm-tool sign');
  }
  if (typeof raw !== 'object' || raw === null) throw new Error('Saída do dm-tool sign inválida');
  const o = raw as Record<string, unknown>;
  const { component, version, os, arch, sha256, signature, size_bytes: size } = o;
  if (component !== 'agent' && component !== 'watchdog') throw new Error('Componente deve ser agent ou watchdog');
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/.test(version)) {
    throw new Error('Versão inválida na saída do dm-tool sign');
  }
  if (typeof os !== 'string' || typeof arch !== 'string' || !TARGETS[os]?.includes(arch)) {
    throw new Error('Sistema/arquitetura fora dos alvos de build');
  }
  if (typeof sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(sha256)) throw new Error('sha256 inválido');
  if (typeof signature !== 'string' || signature.length < 20) throw new Error('Assinatura ausente');
  if (typeof size !== 'number' || size <= 0) throw new Error('Tamanho do arquivo ausente');
  return {
    component,
    version,
    os: os as SignedRelease['os'],
    arch: arch as SignedRelease['arch'],
    sha256,
    signature,
    size_bytes: size,
  };
}

/** sha256 (hex) de um arquivo, calculado no navegador. */
export async function sha256Hex(data: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await data.arrayBuffer());
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
