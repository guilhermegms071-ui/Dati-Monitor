import { ApiError, authFetch, session, unwrap } from './api';

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const tokenResponse = { access_token: 'novo', token_type: 'bearer', expires_in: 900, user: {}, limited: null };

afterEach(() => {
  session.set(null);
  session.onUnauthorized(null);
  vi.unstubAllGlobals();
});

describe('authFetch', () => {
  it('renova a sessão uma única vez para várias chamadas com 401 e repete cada uma', async () => {
    session.set('velho');
    let refreshes = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL | Request) => {
        const req = input instanceof Request ? input : null;
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (url.endsWith('/api/v1/auth/refresh')) {
          refreshes++;
          return Promise.resolve(json(200, tokenResponse));
        }
        const auth = req?.headers.get('Authorization');
        return Promise.resolve(auth === 'Bearer novo' ? json(200, { ok: true }) : json(401, {}));
      }),
    );
    const [a, b] = await Promise.all([
      authFetch(new Request('http://localhost/api/v1/park')),
      authFetch(new Request('http://localhost/api/v1/agents')),
    ]);
    expect(a.status).toBe(200);
    expect(b.status).toBe(200);
    expect(refreshes).toBe(1);
    expect(session.token).toBe('novo');
  });

  it('refresh recusado: avisa o app para voltar ao login', async () => {
    session.set('velho');
    const onUnauthorized = vi.fn();
    session.onUnauthorized(onUnauthorized);
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(json(401, {}))),
    );
    const resp = await authFetch(new Request('http://localhost/api/v1/park'));
    expect(resp.status).toBe(401);
    expect(onUnauthorized).toHaveBeenCalledOnce();
    expect(session.token).toBeNull();
  });
});

describe('unwrap', () => {
  it('transforma o erro padronizado da API em ApiError com código e mensagem', async () => {
    const response = json(409, { detail: { code: 'site_has_agents', message: 'O local tem coletores' } });
    const err: unknown = await unwrap(Promise.resolve({ error: {}, response })).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: 'site_has_agents', message: 'O local tem coletores' });
  });

  it('resposta sem JSON ainda vira um erro legível', async () => {
    const response = new Response('Bad Gateway', { status: 502 });
    await expect(unwrap(Promise.resolve({ response }))).rejects.toMatchObject({
      code: 'http_502',
      message: 'A API respondeu HTTP 502',
    });
  });

  it('devolve os dados quando a resposta é OK', async () => {
    await expect(unwrap(Promise.resolve({ data: { a: 1 }, response: json(200, { a: 1 }) }))).resolves.toEqual({
      a: 1,
    });
  });
});
