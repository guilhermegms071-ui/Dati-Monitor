import { useEffect, useState } from 'react';

import { fetchHealth, type HealthResponse } from '../api/health';

type State =
  | { kind: 'loading' }
  | { kind: 'ok'; health: HealthResponse }
  | { kind: 'degraded'; health: HealthResponse }
  | { kind: 'error'; message: string };

interface Props {
  load?: (signal: AbortSignal) => Promise<HealthResponse>;
}

export function ApiStatus({ load = fetchHealth }: Props) {
  const [state, setState] = useState<State>({ kind: 'loading' });

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal)
      .then((health) => {
        if (health.status === 'ok') {
          setState({ kind: 'ok', health });
        } else {
          console.error('API degradada: banco indisponível', health.database.error);
          setState({ kind: 'degraded', health });
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const message = err instanceof Error ? err.message : String(err);
        console.error('Falha ao consultar a API:', err);
        setState({ kind: 'error', message });
      });
    return () => {
      controller.abort();
    };
  }, [load]);

  switch (state.kind) {
    case 'loading':
      return (
        <p role="status" className="text-slate-500">
          Verificando a API…
        </p>
      );
    case 'ok':
      return (
        <p role="status" className="text-green-700">
          API: conectada (versão {state.health.version}, PostgreSQL {state.health.database.server_version})
        </p>
      );
    case 'degraded':
      return (
        <p role="alert" className="text-orange-600">
          API: no ar, mas o banco de dados está indisponível — {state.health.database.error}
        </p>
      );
    case 'error':
      return (
        <p role="alert" className="text-red-700">
          API: sem conexão — {state.message}
        </p>
      );
  }
}
