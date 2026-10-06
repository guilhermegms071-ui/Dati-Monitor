import createClient from 'openapi-fetch';

import type { components, paths } from '../api/schema';

export type Schemas = components['schemas'];
export type Me = Schemas['MeResponse'];
export type TokenResponse = Schemas['SessionResponse'];

/** Erro da API com o código estável e a mensagem em português devolvidos pelo backend. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

// ----------------------------------------------------------------------------- sessão

let accessToken: string | null = null;
const tokenListeners = new Set<(token: string | null) => void>();
let unauthorizedHandler: (() => void) | null = null;

export const session = {
  get token(): string | null {
    return accessToken;
  },
  set(token: string | null): void {
    accessToken = token;
    tokenListeners.forEach((fn) => {
      fn(token);
    });
  },
  subscribe(fn: (token: string | null) => void): () => void {
    tokenListeners.add(fn);
    return () => tokenListeners.delete(fn);
  },
  /** Chamado quando a sessão expira de vez (refresh recusado): o app volta para o login. */
  onUnauthorized(fn: (() => void) | null): void {
    unauthorizedHandler = fn;
  },
};

export function csrfToken(): string {
  const m = /(?:^|;\s*)dm_csrf=([^;]+)/.exec(document.cookie);
  return m?.[1] ? decodeURIComponent(m[1]) : '';
}

/** Erro padronizado da API (`{"detail": {"code", "message", ...}}`); sem esse formato, mensagem com o status. */
function apiError(status: number, body: unknown): ApiError {
  let code = `http_${String(status)}`;
  let message = `A API respondeu HTTP ${String(status)}`;
  let details: Record<string, unknown> = {};
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = body.detail;
    if (typeof detail === 'object' && detail !== null) {
      const d = detail as Record<string, unknown>;
      if (typeof d.code === 'string') code = d.code;
      if (typeof d.message === 'string') message = d.message;
      details = d;
    }
  }
  return new ApiError(status, code, message, details);
}

async function parseError(resp: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await resp.clone().json();
  } catch {
    // corpo sem JSON (ou já lido): fica a mensagem genérica com o status
  }
  return apiError(resp.status, body);
}

let refreshing: Promise<TokenResponse | null> | null = null;
const REFRESH_TIMEOUT_MS = 15_000;

/** Renova a sessão pelo cookie httpOnly + CSRF (uma renovação por vez, mesmo com várias chamadas). */
export function refreshSession(): Promise<TokenResponse | null> {
  refreshing ??= (async () => {
    try {
      const resp = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': csrfToken(), Accept: 'application/json' },
        // API travada (ex.: recarregando): em vez de "Carregando…" para sempre, vai para o login, que mostra
        // "API: sem conexão" com o motivo.
        signal: AbortSignal.timeout(REFRESH_TIMEOUT_MS),
      });
      if (!resp.ok) {
        session.set(null);
        return null;
      }
      const body = (await resp.json()) as TokenResponse;
      session.set(body.access_token);
      return body;
    } catch (err) {
      console.error('Falha ao renovar a sessão:', err);
      session.set(null);
      return null;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function send(request: Request): Promise<Response> {
  if (accessToken) request.headers.set('Authorization', `Bearer ${accessToken}`);
  return fetch(request);
}

/** fetch com o token de acesso; em 401 tenta renovar a sessão uma vez e repete a chamada. */
export async function authFetch(request: Request): Promise<Response> {
  const retry = request.clone();
  const resp = await send(request);
  if (resp.status !== 401 || request.url.includes('/api/v1/auth/')) return resp;
  const renewed = await refreshSession();
  if (!renewed) {
    unauthorizedHandler?.();
    return resp;
  }
  return send(retry);
}

export const api = createClient<paths>({ baseUrl: '', fetch: authFetch, credentials: 'include' });

/** Resultado do openapi-fetch → dados ou ApiError (a falha nunca é silenciosa). */
export async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (!response.ok) {
    // O openapi-fetch já leu o corpo do erro (em `error`) e a resposta não pode ser lida de novo; sem o
    // erro padronizado nele, tenta a resposta (fica a mensagem com o status se ela já foi consumida).
    if (typeof error === 'object' && error !== null && 'detail' in error) throw apiError(response.status, error);
    throw await parseError(response);
  }
  return data as T;
}

/** Baixa um arquivo autenticado (exportações CSV/XLSX, logs, walks). */
export async function downloadFile(url: string, fallbackName: string): Promise<void> {
  const resp = await authFetch(new Request(url, { credentials: 'include' }));
  if (!resp.ok) throw await parseError(resp);
  const disposition = resp.headers.get('Content-Disposition') ?? '';
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? fallbackName;
  const blob = await resp.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => {
    URL.revokeObjectURL(href);
  }, 10_000);
}

/** Texto autenticado (ex.: últimas linhas de log). */
export async function fetchText(url: string): Promise<string> {
  const resp = await authFetch(new Request(url, { credentials: 'include' }));
  if (!resp.ok) throw await parseError(resp);
  return resp.text();
}

/** Envia um arquivo binário no corpo (publicação de versão) e devolve o JSON da resposta. */
export async function postBinary<T>(url: string, body: Blob): Promise<T> {
  const resp = await authFetch(
    new Request(url, {
      method: 'POST',
      body,
      credentials: 'include',
      headers: { 'Content-Type': 'application/octet-stream', Accept: 'application/json' },
    }),
  );
  if (!resp.ok) throw await parseError(resp);
  return (await resp.json()) as T;
}
