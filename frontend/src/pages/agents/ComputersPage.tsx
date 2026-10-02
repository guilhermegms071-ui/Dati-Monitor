import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { AgentState } from '../../components/domain';
import { LoadMore } from '../../components/paging';
import { Input } from '../../components/ui/form';
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Spinner,
} from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtInt } from '../../lib/format';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';
import { printerName } from '../../lib/printers';

import { ManualReadingButton } from '../park/ManualReading';

type Computer = Schemas['ComputerOut'];

export function ComputersPage() {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const { query, rows } = useCursorList<Computer>(['computers', q], (cursor) =>
    unwrap(api.GET('/api/v1/computers', { params: { query: { q: q || null, limit: PAGE_SIZE, cursor } } })),
  );
  return (
    <div className="space-y-4">
      <PageHeader title="Computadores" subtitle="PCs com o coletor instalado e as impressoras USB ligadas a cada um." />
      <Card className="p-3">
        <div className="relative max-w-md">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" aria-hidden />
          <Input
            aria-label="Buscar computador"
            className="pl-8"
            placeholder="Nome ou hostname"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
          />
        </div>
      </Card>
      <Card>
        {query.isPending ? (
          <Spinner />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState title="Nenhum computador com coletor" />
        ) : (
          <div className="divide-y divide-slate-100 dark:divide-slate-800" data-testid="computers">
            {rows.map((c) => (
              <div key={c.id}>
                <button
                  type="button"
                  className="flex w-full flex-wrap items-center gap-3 px-4 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800"
                  onClick={() => {
                    setOpen(open === c.id ? null : c.id);
                  }}
                  aria-expanded={open === c.id}
                >
                  <span className="min-w-48 flex-1">
                    <span className="block font-medium">{c.name}</span>
                    <span className="block text-xs text-slate-500">
                      {c.hostname ?? '—'} · {c.os ?? c.kind} · {c.location}
                    </span>
                  </span>
                  <AgentState state={c.state} />
                  <span className="text-xs text-slate-500">
                    Último sinal <RelativeTime value={c.last_seen_at} />
                  </span>
                  <Badge tone={c.usb_printers ? 'blue' : 'gray'}>{fmtInt(c.usb_printers)} impressora(s) USB</Badge>
                  <Link
                    className="text-xs text-brand-600 hover:underline"
                    to={`/coletores/${c.id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                    }}
                  >
                    coletor
                  </Link>
                </button>
                {open === c.id ? <UsbPrinters agentId={c.id} /> : null}
              </div>
            ))}
          </div>
        )}
        <LoadMore query={query} shown={rows.length} />
      </Card>
    </div>
  );
}

function UsbPrinters({ agentId }: { agentId: string }) {
  const { can } = useAuth();
  const q = useQuery({
    queryKey: ['computers', agentId, 'usb'],
    queryFn: () =>
      unwrap(api.GET('/api/v1/computers/{agent_id}/usb-printers', { params: { path: { agent_id: agentId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data.length)
    return (
      <p className="px-4 pb-4 text-sm text-slate-500">Nenhuma impressora USB neste PC (só impressoras de rede).</p>
    );
  return (
    <Card className="mx-4 mb-4">
      <CardHeader title="Impressoras USB" />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
            <tr>
              <th className="px-3 py-2 text-left">Impressora</th>
              <th className="px-3 py-2 text-left">Serial</th>
              <th className="px-3 py-2 text-left">Contador</th>
              <th className="px-3 py-2 text-right">Total</th>
              <th className="px-3 py-2 text-left">Última leitura</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {q.data.map((p) => (
              <tr key={p.id} className="border-t border-slate-100 dark:border-slate-800">
                <td className="px-3 py-1.5">
                  <Link className="text-brand-600 hover:underline" to={`/parque/${p.id}`}>
                    {printerName(p.brand, p.model) || p.serial}
                  </Link>
                  <span className="block text-xs text-slate-500">{p.name}</span>
                </td>
                <td className="px-3 py-1.5 font-mono text-xs">{p.serial}</td>
                <td className="px-3 py-1.5">
                  {p.counter_available ? (
                    <Badge tone="green">automático (PJL)</Badge>
                  ) : (
                    <Badge tone="yellow">sem contador disponível</Badge>
                  )}
                  {p.discovery_state === 'pending' ? <Badge tone="yellow">pendente em Descobertas</Badge> : null}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(p.last_total)}</td>
                <td className="px-3 py-1.5">
                  <RelativeTime value={p.last_read_at} />
                </td>
                <td className="px-3 py-1.5 text-right">
                  {can('readings.adjust') ? <ManualReadingButton deviceId={p.id} label="Registrar leitura" /> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
