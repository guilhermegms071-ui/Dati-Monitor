import { formatCep, lookupCep, normalizeCep } from './viacep';

function fakeFetch(status: number, body: unknown): typeof fetch {
  return () => Promise.resolve(new Response(JSON.stringify(body), { status }));
}

describe('ViaCEP', () => {
  it('normaliza e formata o CEP', () => {
    expect(normalizeCep('20040-002')).toBe('20040002');
    expect(normalizeCep('2004')).toBeNull();
    expect(formatCep('20040002')).toBe('20040-002');
  });

  it('preenche o endereço', async () => {
    const addr = await lookupCep(
      '20040-002',
      fakeFetch(200, {
        cep: '20040-002',
        logradouro: 'Avenida Rio Branco',
        bairro: 'Centro',
        localidade: 'Rio de Janeiro',
        uf: 'RJ',
      }),
    );
    expect(addr).toEqual({
      cep: '20040002',
      street: 'Avenida Rio Branco',
      district: 'Centro',
      city: 'Rio de Janeiro',
      state: 'RJ',
    });
  });

  it('explica cada falha', async () => {
    await expect(lookupCep('123', fakeFetch(200, {}))).rejects.toThrow('8 dígitos');
    await expect(lookupCep('99999999', fakeFetch(200, { erro: 'true' }))).rejects.toThrow('99999-999 não encontrado');
    await expect(lookupCep('20040002', fakeFetch(500, {}))).rejects.toThrow('ViaCEP respondeu 500');
    const offline = (() => Promise.reject(new TypeError('Failed to fetch'))) as typeof fetch;
    await expect(lookupCep('20040002', offline)).rejects.toThrow(
      'Não foi possível consultar o ViaCEP: Failed to fetch',
    );
  });
});
