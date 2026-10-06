import { useQuery } from '@tanstack/react-query';
import { Copy, Crown, Radio, WifiOff } from 'lucide-react';
import type { ReactNode } from 'react';

import { api, unwrap, type Schemas } from '../lib/api';
import { fmtDateTime, fmtPercent } from '../lib/format';
import { AGENT_STATE, COMMAND_STATE, DEVICE_STATUS, FINAL_COMMAND_STATES, SUPPLY_COLOR } from '../lib/labels';
import { useSendCommand, type CommandType } from '../lib/commands';
import { detailRows, deviceRows } from '../lib/commandDetails';
import { commandSummary } from '../lib/commandSummary';
import { showError, showSuccess } from '../lib/notify';
import { cn } from '../lib/utils';
import { Button } from './ui/button';
import { Dialog } from './ui/dialog';
import { Badge, Spinner, Tooltip } from './ui/primitives';

type Command = Schemas['CommandOut'];

export function DeviceStatus({ status, disconnected }: { status: string; disconnected?: boolean }) {
  if (disconnected) {
    return (
      <Badge tone="red">
        <WifiOff className="h-3 w-3" aria-hidden /> Sem conexão
      </Badge>
    );
  }
  const s = DEVICE_STATUS[status] ?? { label: status, tone: 'gray' as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

export function AgentState({ state, wsConnected }: { state: string; wsConnected?: boolean }) {
  const s = AGENT_STATE[state] ?? { label: state, tone: 'gray' as const };
  return (
    <span className="inline-flex items-center gap-1.5">
      <Badge tone={s.tone}>{s.label}</Badge>
      {wsConnected ? (
        <Tooltip content="Canal WebSocket conectado (comandos em tempo real)">
          <Radio className="h-3.5 w-3.5 text-emerald-600" aria-label="WebSocket conectado" />
        </Tooltip>
      ) : null}
    </span>
  );
}

export function RoleBadge({ role }: { role: string }) {
  return role === 'master' ? (
    <Badge tone="blue">
      <Crown className="h-3 w-3" aria-hidden /> MASTER
    </Badge>
  ) : (
    <Badge>STANDBY</Badge>
  );
}

export function CommandState({ state }: { state: string }) {
  const s = COMMAND_STATE[state] ?? { label: state, tone: 'gray' as const };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

/** Coluna "Níveis": barras verticais C/M/Y/K com % ("n/d" quando a impressora não informa). */
export function SupplyBars({ supplies }: { supplies: Schemas['SupplyLevel'][] }) {
  if (!supplies.length) return <span className="text-xs text-slate-400">—</span>;
  return (
    <div className="flex items-end gap-1.5">
      {supplies.map((s) => {
        const c = SUPPLY_COLOR[s.color] ?? { short: s.color, bar: 'bg-slate-400' };
        const pct = s.percent ?? null;
        const low = pct !== null && pct <= 10;
        return (
          <Tooltip key={s.color} content={`${s.description ?? c.short}: ${fmtPercent(pct)}`}>
            <div className="flex w-7 flex-col items-center gap-0.5">
              <div className="relative h-8 w-3 overflow-hidden rounded-sm bg-slate-200 dark:bg-slate-700">
                {pct !== null ? (
                  <div
                    className={cn('absolute bottom-0 w-full', c.bar)}
                    style={{ height: `${String(Math.max(pct, 3))}%` }}
                  />
                ) : null}
              </div>
              <span className={cn('text-[10px] leading-none', low ? 'font-bold text-red-600' : 'text-slate-500')}>
                {pct === null ? 'n/d' : `${String(Math.round(pct))}%`}
              </span>
            </div>
          </Tooltip>
        );
      })}
    </div>
  );
}

/** Resultado de um comando, só em português: resumo, equipamentos em tabela, demais dados como lista. */
function CommandResult({ cmd }: { cmd: Command }) {
  const result = cmd.result ?? {};
  const error = typeof result.error === 'string' ? result.error : null;
  const summary = commandSummary(cmd.type, result);
  const devices = deviceRows(result);
  // Com resumo, os números já estão nas frases; a lista mostra só o que o resumo não cobre.
  const covered = summary.length ? Object.keys(result) : [];
  const rows = detailRows(result, ['error', 'devices', 'not_found', ...covered]);
  const hasData = Object.keys(result).length > 0;
  return (
    <div className="space-y-3">
      {error ? (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300" role="alert">
          {error}
        </p>
      ) : null}
      {summary.length ? (
        <ul className="space-y-1 rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-900">
          {summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      {devices.length ? (
        <div className="overflow-hidden rounded-md border border-slate-200 dark:border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 dark:bg-slate-900">
              <tr>
                <th className="px-3 py-2 text-left">Nº de série</th>
                <th className="px-3 py-2 text-left">IP</th>
                <th className="px-3 py-2 text-left">Resultado</th>
              </tr>
            </thead>
            <tbody>
              {devices.map((d, i) => (
                <tr
                  key={`${d.serial}-${d.ip}-${String(i)}`}
                  className="border-t border-slate-100 dark:border-slate-800"
                >
                  <td className="px-3 py-2 font-mono text-xs">{d.serial}</td>
                  <td className="px-3 py-2">{d.ip}</td>
                  <td className="px-3 py-2">
                    {d.ok ? (
                      <Badge tone="green">Lido</Badge>
                    ) : (
                      <span className="text-red-600">{d.error ?? 'Falhou'}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {rows.length ? (
        <dl className="divide-y divide-slate-100 rounded-md border border-slate-200 text-sm dark:divide-slate-800 dark:border-slate-800">
          {rows.map((r) => (
            <div key={r.label} className="flex justify-between gap-4 px-3 py-2">
              <dt className="text-slate-500">{r.label}</dt>
              <dd className="text-right font-medium text-slate-900 dark:text-slate-100">{r.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {cmd.output ? (
        <p className="whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-sm text-slate-700 dark:bg-slate-900 dark:text-slate-300">
          {cmd.output}
        </p>
      ) : null}
      {hasData ? (
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              navigator.clipboard
                .writeText(JSON.stringify(result, null, 2))
                .then(() => {
                  showSuccess('Dados técnicos copiados (para enviar ao suporte)');
                })
                .catch((err: unknown) => {
                  showError(err, 'Não foi possível copiar');
                });
            }}
          >
            <Copy className="h-3.5 w-3.5" /> Copiar dados técnicos
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** Dados de evento/auditoria como lista "rótulo: valor" em português (no lugar de JSON). */
export function DataList({ data, compact }: { data: Record<string, unknown>; compact?: boolean }) {
  const rows = detailRows(data);
  if (!rows.length) return null;
  return (
    <dl className={cn('space-y-0.5', compact ? 'text-xs' : 'text-sm')}>
      {rows.map((r) => (
        <div key={r.label} className="flex gap-2">
          <dt className="shrink-0 text-slate-500">{r.label}:</dt>
          <dd className="min-w-0 break-words font-medium text-slate-800 dark:text-slate-200">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Acompanha um comando ao vivo (eventos SSE invalidam a consulta; polling curto de segurança). */
export function CommandWatch({ commandId, onClose }: { commandId: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['command', commandId],
    queryFn: () => unwrap(api.GET('/api/v1/commands/{command_id}', { params: { path: { command_id: commandId } } })),
    refetchInterval: (query) => (query.state.data && FINAL_COMMAND_STATES.has(query.state.data.state) ? false : 2000),
  });
  const cmd = q.data;
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title={cmd ? `Comando: ${cmd.type_label}` : 'Comando'}
      wide
    >
      {!cmd ? (
        <Spinner />
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <CommandState state={cmd.state} />
            {cmd.progress && !FINAL_COMMAND_STATES.has(cmd.state) ? (
              <span className="text-slate-500">{cmd.progress}</span>
            ) : null}
            <span className="text-slate-400">criado {fmtDateTime(cmd.created_at)}</span>
            {cmd.finished_at ? <span className="text-slate-400">concluído {fmtDateTime(cmd.finished_at)}</span> : null}
          </div>
          {FINAL_COMMAND_STATES.has(cmd.state) ? (
            <CommandResult cmd={cmd} />
          ) : (
            <Spinner label="Aguardando o coletor…" />
          )}
        </div>
      )}
    </Dialog>
  );
}

export function CommandButton({
  agentId,
  type,
  params,
  children,
  variant = 'secondary',
}: {
  agentId: string;
  type: CommandType;
  params?: Record<string, unknown>;
  children: ReactNode;
  variant?: 'secondary' | 'primary' | 'ghost';
}) {
  const { send, busy, watcher } = useSendCommand(agentId);
  return (
    <>
      <Button size="sm" variant={variant} loading={busy} onClick={() => void send(type, params)}>
        {children}
      </Button>
      {watcher}
    </>
  );
}
