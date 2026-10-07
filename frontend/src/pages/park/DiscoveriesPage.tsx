import { useQueryClient } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { LoadMore } from '../../components/paging';
import { Button } from '../../components/ui/button';
import { ConfirmButton } from '../../components/ui/dialog';
import { Input } from '../../components/ui/form';
import {
  Card,
  Checkbox,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Spinner,
  Tabs,
  TabsList,
  TabsTrigger,
} from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtInt } from '../../lib/format';
import { printerName } from '../../lib/printers';
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';
import { TransfersPanel } from './TransfersPanel';

type Row = Schemas['ParkRow'];
type State = 'pending' | 'discarded';
type Action = 'approve' | 'discard' | 'restore';

const DONE: Record<Action, string> = {
  approve: 'ativado(s)',
  discard: 'descartado(s)',
  restore: 'de volta às pendências',
};

type Tab = State | 'transfers';

/** Equipamentos > Descobertas (seção 16.1): impressoras novas esperam aqui até alguém ativar ou descartar;
 * em Transferências, as que apareceram num local de outro cliente esperam a aprovação. */
export function DiscoveriesPage() {
  const [tab, setTab] = useState<Tab>('pending');
  return (
    <div className="space-y-3">
      <PageHeader
        title="Descobertas"
        subtitle="Impressoras encontradas pelos coletores. Só entram no parque, nos relatórios e nos alertas depois de ativadas."
      />
      <Tabs
        value={tab}
        onValueChange={(v) => {
          setTab(v as Tab);
        }}
      >
        <TabsList>
          <TabsTrigger value="pending">Pendentes</TabsTrigger>
          <TabsTrigger value="discarded">Descartados</TabsTrigger>
          <TabsTrigger value="transfers">Transferências</TabsTrigger>
        </TabsList>
      </Tabs>
      {tab === 'transfers' ? <TransfersPanel /> : <DiscoveryList key={tab} state={tab} />}
    </div>
  );
}

function DiscoveryList({ state }: { state: State }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const { query, rows, total } = useCursorList<Row>(['discoveries', state, q], (cursor) =>
    unwrap(api.GET('/api/v1/discoveries', { params: { query: { state, q: q || null, limit: PAGE_SIZE, cursor } } })),
  );

  const decide = (action: Action, ids: string[]) => {
    setBusy(true);
    return unwrap(api.POST('/api/v1/discoveries/decide', { body: { action, device_ids: ids } }))
      .then((r) => {
        const skipped = r.skipped.length ? `; ${String(r.skipped.length)} ignorado(s)` : '';
        showSuccess(`${fmtInt(r.changed)} equipamento(s) ${DONE[action]}${skipped}`);
        setSelected(new Set());
        void qc.invalidateQueries({ queryKey: ['discoveries'] });
        void qc.invalidateQueries({ queryKey: ['discovery-counts'] });
        void qc.invalidateQueries({ queryKey: ['park'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Não foi possível concluir');
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const canApprove = can('devices.create');
  const canDiscard = can('devices.delete');
  const canRestore = can('devices.update');
  const ids = [...selected];
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));

  return (
    <div className="space-y-3">
      <Card className="flex flex-wrap items-center gap-3 p-3">
        <div className="relative min-w-60 flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            aria-hidden
          />
          <Input
            aria-label="Pesquisar descobertas"
            className="pl-8"
            placeholder="Serial, IP, modelo, cliente, local…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
          />
        </div>
        {ids.length ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-slate-500">{fmtInt(ids.length)} selecionado(s)</span>
            {state === 'pending' && canApprove ? (
              <Button size="sm" loading={busy} onClick={() => void decide('approve', ids)}>
                Ativar selecionados
              </Button>
            ) : null}
            {state === 'pending' && canDiscard ? (
              <ConfirmButton
                title="Descartar equipamentos"
                description="Os coletores deixam de ler estes equipamentos. Dá para restaurá-los depois em Descartados."
                danger
                confirmLabel="Descartar"
                onConfirm={() => decide('discard', ids)}
              >
                Descartar selecionados
              </ConfirmButton>
            ) : null}
            {state === 'discarded' && canRestore ? (
              <Button size="sm" variant="secondary" loading={busy} onClick={() => void decide('restore', ids)}>
                Restaurar selecionados
              </Button>
            ) : null}
          </div>
        ) : null}
      </Card>
      <Card className="overflow-x-auto">
        {query.isPending ? (
          <Spinner />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : !rows.length ? (
          <EmptyState title={state === 'pending' ? 'Nenhum equipamento aguardando' : 'Nenhum equipamento descartado'} />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="w-8 px-3 py-2">
                  <Checkbox
                    label="Selecionar todos"
                    checked={allSelected ? true : selected.size ? 'indeterminate' : false}
                    onCheckedChange={(v) => {
                      setSelected(v ? new Set(rows.map((r) => r.id)) : new Set());
                    }}
                  />
                </th>
                <th className="px-3 py-2 text-left">Descoberta</th>
                <th className="px-3 py-2 text-left">IP</th>
                <th className="px-3 py-2 text-left">Serial</th>
                <th className="px-3 py-2 text-left">Marca / modelo</th>
                <th className="px-3 py-2 text-left">Setor</th>
                <th className="px-3 py-2 text-left">Cliente / local</th>
                <th className="px-3 py-2 text-left">Coletor</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-1.5">
                    <Checkbox
                      label={`Selecionar ${d.serial}`}
                      checked={selected.has(d.id)}
                      onCheckedChange={(v) => {
                        setSelected((s) => {
                          const n = new Set(s);
                          if (v) n.add(d.id);
                          else n.delete(d.id);
                          return n;
                        });
                      }}
                    />
                  </td>
                  <td className="px-3 py-1.5 text-xs">
                    <RelativeTime value={d.first_seen_at} />
                  </td>
                  <td className="px-3 py-1.5 font-mono text-xs">{d.ip ?? '—'}</td>
                  <td className="px-3 py-1.5 font-mono text-xs">
                    <Link to={`/parque/${d.id}`} className="text-brand-600 hover:underline dark:text-brand-100">
                      {d.serial}
                    </Link>
                  </td>
                  <td className="px-3 py-1.5 text-xs">{printerName(d.brand, d.model) || '—'}</td>
                  <td className="px-3 py-1.5 text-xs">{d.sector ?? '—'}</td>
                  <td className="px-3 py-1.5 text-xs">
                    {d.customer_name} / {d.site_name}
                  </td>
                  <td className="px-3 py-1.5 text-xs">{d.agent_name ?? '—'}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(d.last_total)}</td>
                  <td className="px-3 py-1.5 text-right">
                    <span className="flex justify-end gap-1">
                      {state === 'pending' && canApprove ? (
                        <Button size="sm" loading={busy} onClick={() => void decide('approve', [d.id])}>
                          Ativar
                        </Button>
                      ) : null}
                      {state === 'pending' && canDiscard ? (
                        <ConfirmButton
                          title="Descartar equipamento"
                          description={`O coletor deixa de ler o equipamento ${d.serial}.`}
                          danger
                          confirmLabel="Descartar"
                          onConfirm={() => decide('discard', [d.id])}
                        >
                          Descartar
                        </ConfirmButton>
                      ) : null}
                      {state === 'discarded' && canApprove ? (
                        <Button size="sm" loading={busy} onClick={() => void decide('approve', [d.id])}>
                          Ativar
                        </Button>
                      ) : null}
                      {state === 'discarded' && canRestore ? (
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={busy}
                          onClick={() => void decide('restore', [d.id])}
                        >
                          Restaurar
                        </Button>
                      ) : null}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <LoadMore query={query} shown={rows.length} total={total} />
      </Card>
    </div>
  );
}
