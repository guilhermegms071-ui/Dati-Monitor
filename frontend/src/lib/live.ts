import { useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refreshSession, session } from './api';

export type LiveStatus = 'connecting' | 'live' | 'offline';

export interface LiveEvent {
  type: string;
  id?: string;
  agent_id?: string;
  state?: string;
  [key: string]: unknown;
}

/** Parser incremental de Server-Sent Events (linhas "event:" / "data:" separadas por linha vazia). */
export class SSEParser {
  private buffer = '';
  private event = 'message';
  private data: string[] = [];

  feed(chunk: string, emit: (event: string, data: string) => void): void {
    this.buffer += chunk;
    let idx = this.buffer.indexOf('\n');
    while (idx >= 0) {
      const line = this.buffer.slice(0, idx).replace(/\r$/, '');
      this.buffer = this.buffer.slice(idx + 1);
      if (line === '') {
        if (this.data.length) emit(this.event, this.data.join('\n'));
        this.event = 'message';
        this.data = [];
      } else if (line.startsWith('event:')) {
        this.event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        this.data.push(line.slice(5).trimStart());
      }
      idx = this.buffer.indexOf('\n');
    }
  }
}

/** O que cada evento invalida no cache (a tela recarrega só o que mudou). */
export function applyEvent(qc: QueryClient, kind: string, ev: LiveEvent, devicesThrottle: () => void): void {
  switch (kind) {
    case 'agent':
      void qc.invalidateQueries({ queryKey: ['agents'] });
      if (ev.id) void qc.invalidateQueries({ queryKey: ['agent', ev.id] });
      void qc.invalidateQueries({ queryKey: ['cluster'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
      break;
    case 'command':
      if (ev.agent_id) void qc.invalidateQueries({ queryKey: ['commands', ev.agent_id] });
      if (ev.id) void qc.invalidateQueries({ queryKey: ['command', ev.id] });
      break;
    case 'devices':
      devicesThrottle();
      break;
    case 'alerts':
      void qc.invalidateQueries({ queryKey: ['alerts'] });
      void qc.invalidateQueries({ queryKey: ['alert-counts'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
      break;
    case 'resync':
      void qc.invalidateQueries();
      break;
    default:
      break;
  }
}

function throttle(fn: () => void, ms: number): () => void {
  let timer: ReturnType<typeof setTimeout> | null = null;
  return () => {
    timer ??= setTimeout(() => {
      timer = null;
      fn();
    }, ms);
  };
}

/** Mantém a conexão de eventos ao vivo enquanto houver sessão. */
export function useLiveEvents(enabled: boolean): LiveStatus {
  const qc = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>('connecting');

  useEffect(() => {
    if (!enabled) return;
    // Lido por função: o TypeScript não estreita o valor entre os awaits da reconexão.
    const life = { stopped: false };
    const stopped = () => life.stopped;
    let controller: AbortController | null = null;
    const devices = throttle(() => {
      void qc.invalidateQueries({ queryKey: ['park'] });
      void qc.invalidateQueries({ queryKey: ['device'] });
      void qc.invalidateQueries({ queryKey: ['dashboard'] });
    }, 3000);

    async function connect(attempt: number): Promise<void> {
      if (stopped()) return;
      controller = new AbortController();
      setStatus('connecting');
      try {
        const resp = await fetch('/api/v1/events', {
          headers: { Authorization: `Bearer ${session.token ?? ''}`, Accept: 'text/event-stream' },
          signal: controller.signal,
        });
        if (resp.status === 401) {
          await refreshSession();
          throw new Error('sessão renovada; reconectando');
        }
        if (!resp.ok || !resp.body) throw new Error(`HTTP ${String(resp.status)}`);
        const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
        const parser = new SSEParser();
        setStatus('live');
        attempt = 0;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          parser.feed(value, (event, data) => {
            try {
              applyEvent(qc, event, JSON.parse(data) as LiveEvent, devices);
            } catch (err) {
              console.error('Evento ao vivo inválido:', event, data, err);
            }
          });
        }
        throw new Error('conexão encerrada pelo servidor');
      } catch (err) {
        if (stopped()) return;
        setStatus('offline');
        const wait = Math.min(30_000, 1000 * 2 ** attempt) * (0.8 + Math.random() * 0.4);
        console.warn(
          `Eventos ao vivo desconectados (${err instanceof Error ? err.message : String(err)}); nova tentativa em ${String(Math.round(wait / 1000))} s`,
        );
        setTimeout(() => {
          void connect(attempt + 1);
        }, wait);
      }
    }

    void connect(0);
    return () => {
      life.stopped = true;
      controller?.abort();
    };
  }, [enabled, qc]);

  return enabled ? status : 'offline';
}
