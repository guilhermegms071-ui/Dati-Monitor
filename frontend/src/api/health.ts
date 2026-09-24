export interface DatabaseHealth {
  ok: boolean;
  latency_ms: number | null;
  server_version: string | null;
  error: string | null;
}

export interface HealthResponse {
  status: 'ok' | 'degraded';
  service: string;
  product: string;
  version: string;
  database: DatabaseHealth;
}

function isHealthResponse(value: unknown): value is HealthResponse {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    (v.status === 'ok' || v.status === 'degraded') &&
    typeof v.version === 'string' &&
    typeof v.database === 'object' &&
    v.database !== null
  );
}

/**
 * Consulta GET /api/health. A API responde 503 com corpo válido quando o banco está fora;
 * nesse caso o corpo é devolvido (status "degraded"). Qualquer outra falha vira exceção.
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const resp = await fetch('/api/health', { signal, headers: { Accept: 'application/json' } });
  let body: unknown;
  try {
    body = await resp.json();
  } catch {
    throw new Error(`API respondeu HTTP ${String(resp.status)} sem JSON válido`);
  }
  if (!isHealthResponse(body)) {
    throw new Error(`API respondeu HTTP ${String(resp.status)} com formato inesperado`);
  }
  return body;
}
