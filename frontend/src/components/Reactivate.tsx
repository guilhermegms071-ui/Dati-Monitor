import { useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Info, RotateCcw, XCircle } from 'lucide-react';
import { useState } from 'react';

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

/** Botão "Reativar" (ação composta da seção 4.7) com o painel de acompanhamento em tempo real. */
export function ReactivateButton({ agentId, agentName }: { agentId: string; agentName: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Schemas['Reactivation'] | null>(null);

  async function run() {
    setOpen(true);
    setResult(null);
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
