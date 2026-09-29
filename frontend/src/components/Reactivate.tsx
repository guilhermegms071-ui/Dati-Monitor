import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Clock, Info, RotateCcw, XCircle } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api, unwrap, type Schemas } from '../lib/api';
import { FINAL_COMMAND_STATES } from '../lib/labels';
import { showError } from '../lib/notify';
import { CommandState } from './domain';
import { Button } from './ui/button';
import { Dialog } from './ui/dialog';
import { Spinner } from './ui/primitives';

function StepRow({ step }: { step: Schemas['ReactivationStep'] }) {
  const q = useQuery({
    queryKey: ['command', step.command_id ?? ''],
    queryFn: () =>
      unwrap(api.GET('/api/v1/commands/{command_id}', { params: { path: { command_id: step.command_id ?? '' } } })),
    enabled: Boolean(step.command_id),
    refetchInterval: (query) => (query.state.data && FINAL_COMMAND_STATES.has(query.state.data.state) ? false : 2000),
  });
  const state = q.data?.state;
  const error = typeof q.data?.result?.error === 'string' ? q.data.result.error : null;
  return (
    <li className="flex items-start gap-3 py-2">
      {state === 'succeeded' ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" />
      ) : state === 'failed' || state === 'expired' ? (
        <XCircle className="mt-0.5 h-4 w-4 text-red-600" />
      ) : (
        <Info className="mt-0.5 h-4 w-4 text-slate-400" />
      )}
      <div className="flex-1 text-sm">
        <p>{step.message}</p>
        {error ? <p className="text-xs text-red-600">{error}</p> : null}
      </div>
      {state ? <CommandState state={state} /> : null}
    </li>
  );
}

/** Etapa 2 (seção 4.7): depois do pedido ao watchdog, acompanha até o coletor voltar (ou o prazo acabar). */
function WaitForAgent({
  agentId,
  requestedAt,
  clickedAt,
  seconds,
}: {
  agentId: string;
  requestedAt: string;
  clickedAt: number;
  seconds: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  const q = useQuery({
    queryKey: ['agent', agentId],
    queryFn: () => unwrap(api.GET('/api/v1/agents/{agent_id}', { params: { path: { agent_id: agentId } } })),
    refetchInterval: 3000,
  });
  const agent = q.data;
  const back =
    agent !== undefined &&
    (agent.state === 'online' || agent.state === 'degraded') &&
    agent.last_seen_at !== null &&
    // Relógio do servidor dos dois lados: o do PC do técnico pode estar errado.
    Date.parse(agent.last_seen_at) > Date.parse(requestedAt);
  // Contagem regressiva pelo relógio local (só o tempo decorrido importa).
  const left = Math.max(0, Math.ceil((clickedAt + seconds * 1000 - now) / 1000));
  const done = back || left === 0;
  useEffect(() => {
    if (done) return;
    const t = setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => {
      clearInterval(t);
    };
  }, [done]);
  if (back) {
    return (
      <p className="flex items-center gap-2 rounded-md bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
        <CheckCircle2 className="h-4 w-4" /> O coletor voltou e está enviando sinal.
      </p>
    );
  }
  if (left === 0) {
    return (
      <p className="rounded-md bg-red-50 p-3 text-sm text-red-800 dark:bg-red-950 dark:text-red-300" role="alert">
        O coletor não voltou em {Math.round(seconds / 60)} min. Abra a aba Saúde para ver o motivo que o vigia informou
        e baixe os logs do watchdog.
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
      <Clock className="h-4 w-4" /> Aguardando o coletor voltar… ({left} s)
    </p>
  );
}

/** Botão "Reativar" (ação composta da seção 4.7) com o painel de acompanhamento em tempo real. */
export function ReactivateButton({ agentId, agentName }: { agentId: string; agentName: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Schemas['Reactivation'] | null>(null);
  const [startedAt, setStartedAt] = useState(0);

  async function run() {
    setOpen(true);
    setResult(null);
    setStartedAt(Date.now());
    setBusy(true);
    try {
      const r = await unwrap(
        api.POST('/api/v1/agents/{agent_id}/reactivate', { params: { path: { agent_id: agentId } } }),
      );
      setResult(r);
      void qc.invalidateQueries({ queryKey: ['commands'] });
    } catch (err) {
      showError(err, 'Não foi possível reativar');
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => void run()} loading={busy && !open}>
        <RotateCcw className="h-3.5 w-3.5" /> Reativar
      </Button>
      <Dialog open={open} onOpenChange={setOpen} title={`Reativar — ${agentName}`}>
        {!result ? (
          <Spinner label="Analisando o coletor e o local…" />
        ) : (
          <div className="space-y-3">
            <p
              className={
                result.outcome === 'nothing_online' ? 'font-medium text-red-700 dark:text-red-400' : 'font-medium'
              }
            >
              {result.message}
            </p>
            {result.steps.length ? (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {result.steps.map((s, i) => (
                  <StepRow key={`${s.action}-${String(i)}`} step={s} />
                ))}
              </ul>
            ) : null}
            {result.wait_seconds > 0 ? (
              <WaitForAgent
                agentId={agentId}
                requestedAt={result.requested_at}
                clickedAt={startedAt}
                seconds={result.wait_seconds}
              />
            ) : null}
            {result.suggestions.length ? (
              <div className="rounded-md bg-slate-50 p-3 text-sm dark:bg-slate-800">
                <p className="mb-1 font-medium">O que fazer agora</p>
                <ul className="list-disc space-y-1 pl-5">
                  {result.suggestions.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        )}
      </Dialog>
    </>
  );
}
