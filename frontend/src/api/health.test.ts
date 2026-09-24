import { fetchHealth } from './health';

function mockFetch(status: number, body: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(new Response(body, { status, headers: { 'Content-Type': 'application/json' } }))),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('fetchHealth', () => {
  it('devolve o corpo quando a API responde 200', async () => {
    mockFetch(
      200,
      JSON.stringify({
        status: 'ok',
        service: 'api',
        product: 'Dati Monitor',
        version: '0.1.0',
        database: { ok: true },
      }),
    );
    await expect(fetchHealth()).resolves.toMatchObject({ status: 'ok', version: '0.1.0' });
  });

  it('devolve o corpo "degraded" quando a API responde 503', async () => {
    mockFetch(
      503,
      JSON.stringify({
        status: 'degraded',
        service: 'api',
        product: 'Dati Monitor',
        version: '0.1.0',
        database: { ok: false },
      }),
    );
    await expect(fetchHealth()).resolves.toMatchObject({ status: 'degraded' });
  });

  it('falha com mensagem clara quando a resposta não é JSON', async () => {
    mockFetch(502, '<html>Bad Gateway</html>');
    await expect(fetchHealth()).rejects.toThrow('API respondeu HTTP 502 sem JSON válido');
  });

  it('falha quando o JSON tem formato inesperado', async () => {
    mockFetch(200, JSON.stringify({ hello: 'world' }));
    await expect(fetchHealth()).rejects.toThrow('formato inesperado');
  });
});
