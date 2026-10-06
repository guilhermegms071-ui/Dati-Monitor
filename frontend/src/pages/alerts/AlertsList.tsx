import { useQueryClient } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { LoadMore } from '../../components/paging';
import { Button } from '../../components/ui/button';
import { Input, Select } from '../../components/ui/form';
import { Badge, Card, Checkbox, EmptyState, ErrorState, RelativeTime, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { fmtInt } from '../../lib/format';
import { showError, showSuccess } from '../../lib/notify';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';

import { SeverityBadge } from './badges';

type Alert = Schemas['AlertOut'];
type State = 'open' | 'acknowledged' | 'resolved' | 'all';

const STATE_LABEL: Record<string, string> = { open: 'Aberto', acknowledged: 'Reconhecido', resolved: 'Resolvido' };

function targetLink(a: Alert): string | null {
  if (a.target_type === 'device') return `/parque/${a.target_id}`;
  if (a.target_type === 'agent') return `/coletores/${a.target_id}`;
  return null;
}

/** Lista de alertas (seção 10, item 8) — usada na tela Alertas e nas abas de cliente e equipamento. */
export function AlertsList({
  customerId,
  deviceId,
  compact = false,
}: {
  customerId?: string;
  deviceId?: string;
  compact?: boolean;
}) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [state, setState] = useState<State>('open');
  const [severity, setSeverity] = useState('');
  const [q, setQ] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const { query, rows, total } = useCursorList<Alert>(
    ['alerts', { state, severity, q, customerId, deviceId }],
    (cursor) =>
      unwrap(
        api.GET('/api/v1/alerts', {
          params: {
            query: {
              state,
              severity: (severity || null) as 'info' | 'warning' | 'critical' | null,
              q: q || null,
              customer_id: customerId ?? null,
              device_id: deviceId ?? null,
              limit: PAGE_SIZE,
              cursor,
            },
          },
        }),
      ),
  );
  const canManage = can('alerts.manage');

  const act = (action: 'acknowledge' | 'resolve', ids: string[]) => {
    setBusy(true);
    unwrap(api.POST('/api/v1/alerts/act', { body: { action, alert_ids: ids } }))
      .then((r) => {
        showSuccess(`${fmtInt(r.changed)} alerta(s) ${action === 'resolve' ? 'resolvido(s)' : 'reconhecido(s)'}`);
        setSelected(new Set());
        void qc.invalidateQueries({ queryKey: ['alerts'] });
        void qc.invalidateQueries({ queryKey: ['alert-counts'] });
        void qc.invalidateQueries({ queryKey: ['dashboard'] });
      })
      .catch((err: unknown) => {
        showError(err, 'Não foi possível concluir');
      })
      .finally(() => {
        setBusy(false);
      });
  };

  const ids = [...selected];
  const allSelected = rows.length > 0 && rows.every((r) => selected.has(r.id));
  return (
    <div className="space-y-3">
      <Card className="flex flex-wrap items-center gap-2 p-3">
        <Select
          aria-label="Situação"
          className="w-40"
          value={state}
          onChange={(e) => {
            setState(e.target.value as State);
            setSelected(new Set());
          }}
        >
          <option value="open">Não resolvidos</option>
          <option value="acknowledged">Reconhecidos</option>
          <option value="resolved">Resolvidos</option>
          <option value="all">Todos</option>
        </Select>
        <Select
          aria-label="Gravidade"
          className="w-52"
          value={severity}
          onChange={(e) => {
            setSeverity(e.target.value);
          }}
        >
          <option value="">Todas as gravidades</option>
          <option value="critical">Crítico</option>
          <option value="warning">Atenção</option>
          <option value="info">Aviso</option>
        </Select>
        {!compact ? (
          <div className="relative min-w-52 flex-1">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
              aria-hidden
            />
            <Input
              aria-label="Pesquisar alertas"
              className="pl-8"
              placeholder="Serial, coletor, mensagem…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
              }}
            />
          </div>
        ) : null}
        {ids.length && canManage ? (
          <span className="flex items-center gap-2">
            <span className="text-sm text-slate-500">{fmtInt(ids.length)} selecionado(s)</span>
            <Button
              size="sm"
              variant="secondary"
              loading={busy}
              onClick={() => {
                act('acknowledge', ids);
              }}
            >
              Reconhecer
            </Button>
            <Button
              size="sm"
              loading={busy}
              onClick={() => {
                act('resolve', ids);
              }}
            >
              Resolver
            </Button>
          </span>
        ) : null}
      </Card>
      <Card className="overflow-x-auto">
        {query.isPending ? (
          <Spinner />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : !rows.length ? (
          <EmptyState title={state === 'open' ? 'Nenhum alerta aberto' : 'Nenhum alerta'} />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                {canManage ? (
                  <th className="w-8 px-3 py-2">
                    <Checkbox
                      label="Selecionar todos"
                      checked={allSelected ? true : selected.size ? 'indeterminate' : false}
                      onCheckedChange={(v) => {
                        setSelected(
                          v ? new Set(rows.filter((r) => r.state !== 'resolved').map((r) => r.id)) : new Set(),
                        );
                      }}
                    />
                  </th>
                ) : null}
                <th className="px-3 py-2 text-left">Gravidade</th>
                <th className="px-3 py-2 text-left">Alerta</th>
                {!compact ? <th className="px-3 py-2 text-left">Cliente</th> : null}
                <th className="px-3 py-2 text-left">Aberto</th>
                <th className="px-3 py-2 text-left">Situação</th>
                {canManage ? <th className="px-3 py-2" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const link = targetLink(a);
                return (
                  <tr key={a.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                    {canManage ? (
                      <td className="px-3 py-2">
                        {a.state !== 'resolved' ? (
                          <Checkbox
                            label={`Selecionar ${a.message}`}
                            checked={selected.has(a.id)}
                            onCheckedChange={(v) => {
                              setSelected((s) => {
                                const n = new Set(s);
                                if (v) n.add(a.id);
                                else n.delete(a.id);
                                return n;
                              });
                            }}
                          />
                        ) : null}
                      </td>
                    ) : null}
                    <td className="px-3 py-2">
                      <SeverityBadge severity={a.severity} />
                    </td>
                    <td className="px-3 py-2">
                      <p className="font-medium">{a.type_label}</p>
                      <p className="text-xs text-slate-600 dark:text-slate-400">
                        {link ? (
                          <Link to={link} className="hover:underline">
                            {a.message}
                          </Link>
                        ) : (
                          a.message
                        )}
                      </p>
                    </td>
                    {!compact ? <td className="px-3 py-2 text-xs">{a.customer_name || '—'}</td> : null}
                    <td className="px-3 py-2 text-xs">
                      <RelativeTime value={a.opened_at} />
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {a.state === 'resolved' ? (
                        <Badge tone="green">{a.data.auto_resolved ? 'Resolvido sozinho' : STATE_LABEL[a.state]}</Badge>
                      ) : (
                        <Badge tone={a.state === 'acknowledged' ? 'blue' : 'yellow'}>{STATE_LABEL[a.state]}</Badge>
                      )}
                    </td>
                    {canManage ? (
                      <td className="px-3 py-2 text-right">
                        {a.state !== 'resolved' ? (
                          <span className="flex justify-end gap-1">
                            {a.state === 'open' ? (
                              <Button
                                size="sm"
                                variant="secondary"
                                loading={busy}
                                onClick={() => {
                                  act('acknowledge', [a.id]);
                                }}
                              >
                                Reconhecer
                              </Button>
                            ) : null}
                            <Button
                              size="sm"
                              variant="secondary"
                              loading={busy}
                              onClick={() => {
                                act('resolve', [a.id]);
                              }}
                            >
                              Resolver
                            </Button>
                          </span>
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <LoadMore query={query} shown={rows.length} total={total} />
      </Card>
    </div>
  );
}
