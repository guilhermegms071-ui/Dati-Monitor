import { parseSignOutput, sha256Hex } from './releases';

const valid = {
  component: 'agent',
  version: '1.2.0',
  os: 'windows',
  arch: 'amd64',
  sha256: 'e7fb9ce69d7397565d939873d452715243aac32844b61fb4f0eeb6eee5e6b22f',
  size_bytes: 12345,
  signature: '76feWlWxFA8QVLX3JvRoS8iV6SzlWfJcsOL4lxud61xzLt4LFxl4HEr9SLTsXrxkiyfhy1e1tcKg+Kkw8bZHAw==',
};

describe('parseSignOutput', () => {
  it('lê o JSON do dm-tool sign', () => {
    expect(parseSignOutput(JSON.stringify(valid, null, 2))).toEqual(valid);
  });

  it('recusa com motivo claro', () => {
    expect(() => parseSignOutput('assinatura: abc')).toThrow('Cole exatamente o JSON impresso pelo dm-tool sign');
    expect(() => parseSignOutput(JSON.stringify({ ...valid, component: 'x' }))).toThrow('agent ou watchdog');
    expect(() => parseSignOutput(JSON.stringify({ ...valid, version: '1.2' }))).toThrow('Versão inválida');
    expect(() => parseSignOutput(JSON.stringify({ ...valid, os: 'linux', arch: 'mips' }))).toThrow('alvos de build');
    expect(() => parseSignOutput(JSON.stringify({ ...valid, os: 'windows', arch: 'arm' }))).toThrow('alvos de build');
    expect(() => parseSignOutput(JSON.stringify({ ...valid, sha256: 'ABC' }))).toThrow('sha256 inválido');
    expect(() => parseSignOutput(JSON.stringify({ ...valid, signature: '' }))).toThrow('Assinatura ausente');
  });
});

describe('sha256Hex', () => {
  it('confere com o vetor do padrão (abc)', async () => {
    await expect(sha256Hex(new Blob(['abc']))).resolves.toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });
});
