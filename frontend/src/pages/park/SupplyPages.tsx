import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';

import { LoadMore } from '../../components/paging';
import { Input, Select } from '../../components/ui/form';
import {
  Badge,
  Card,
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
import { fmtDateTime, fmtInt } from '../../lib/format';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';

const COLOR: Record<string, string> = { black: 'Preto', cyan: 'Ciano', magenta: 'Magenta', yellow: 'Amarelo' };

/** Equipamentos > Trocas de toner (seção 16.3): nível que subiu = cartucho trocado, com rendimento. */
export function ReplacementsPage() {
  const [q, setQ] = useState('');
  const [color, setColor] = useState('');
  const [premature, setPremature] = useState('');
  const { query, rows, total } = useCursorList<Schemas['SupplyReplacementOut']>(
    ['supply-replacements', q, color, premature],
    (cursor) =>
      unwrap(
        api.GET('/api/v1/supply-replacements', {
          params: {
            query: {
              q: q || null,
              color: color || null,
              premature: premature === '' ? null : premature === 'true',
              limit: PAGE_SIZE,
              cursor,
            },
          },
        }),
      ),
  );
  return (
    <div className="space-y-3">
      <PageHeader
        title="Trocas de toner"
        subtitle="Detectadas quando o nível sobe. Rendimento = páginas impressas com o cartucho anterior."
      />
      <Card className="flex flex-wrap items-center gap-2 p-3">
        <div className="relative min-w-52 flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            aria-hidden
          />
          <Input
            aria-label="Pesquisar trocas"
            className="pl-8"
            placeholder="Serial, modelo, cliente…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
          />
        </div>
        <Select
          aria-label="Cor"
          className="w-36"
          value={color}
          onChange={(e) => {
            setColor(e.target.value);
          }}
        >
          <option value="">Todas as cores</option>
          {Object.entries(COLOR).map(([c, label]) => (
            <option key={c} value={c}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Troca prematura"
          className="w-44"
          value={premature}
          onChange={(e) => {
            setPremature(e.target.value);
          }}
        >
          <option value="">Todas as trocas</option>
          <option value="true">Só prematuras (&gt; 20%)</option>
          <option value="false">Só no fim do cartucho</option>
        </Select>
      </Card>
      <Card className="overflow-x-auto">
        {query.isPending ? (
          <Spinner />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : !rows.length ? (
          <EmptyState title="Nenhuma troca registrada" />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Quando</th>
                <th className="px-3 py-2 text-left">Equipamento</th>
                <th className="px-3 py-2 text-left">Suprimento</th>
                <th className="px-3 py-2 text-right">Nível antes → depois</th>
                <th className="px-3 py-2 text-right">Contador na troca</th>
                <th className="px-3 py-2 text-right">Rendimento</th>
                <th className="px-3 py-2 text-left">Cartucho</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                  <td className="px-3 py-2 text-xs" title={fmtDateTime(r.replaced_at)}>
                    <RelativeTime value={r.replaced_at} />
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <Link to={`/parque/${r.device_id}`} className="font-mono text-brand-600 hover:underline">
                      {r.serial}
                    </Link>
                    <span className="block text-slate-500">
                      {r.model ?? ''} · {r.customer_name}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {r.color ? (COLOR[r.color] ?? r.color) : '—'}
                    <span className="block text-slate-500">{r.description ?? r.supply_key}</span>
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {Math.round(Number(r.level_before))}% → {Math.round(Number(r.level_after))}%
                    {r.premature ? (
                      <Badge tone="yellow" className="ml-1">
                        prematura
                      </Badge>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {fmtInt(r.yield_counter === 'color' ? r.color_before : r.total_before)}
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">
                    {r.yield_pages !== null ? `${fmtInt(r.yield_pages)} pág.` : 'sem troca anterior'}
                    {r.nominal_capacity ? (
                      <span className="block text-slate-500">nominal {fmtInt(r.nominal_capacity)}</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {r.cartridge_serial_after ?? '—'}
                    {r.cartridge_serial_before ? (
                      <span className="block text-slate-500">anterior {r.cartridge_serial_before}</span>
                    ) : null}
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

const CATEGORIES: [string, string][] = [
  ['service_call', 'Chamado técnico'],
  ['parts', 'Peças/manutenção'],
  ['jam', 'Atolamento'],
  ['consumable', 'Consumível'],
  ['other', 'Outros'],
];
const SEVERITY: Record<number, string> = { 1: 'outro', 3: 'crítico', 4: 'atenção', 5: 'evento' };

/** Equipamentos > Alertas da impressora (seção 16.4): o que a própria impressora registrou (prtAlertTable). */
export function PrinterAlertsPage() {
  const [category, setCategory] = useState('service_call');
  const [active, setActive] = useState('true');
  const [q, setQ] = useState('');
  const counts = useQuery({
    queryKey: ['printer-alert-counts'],
    queryFn: () => unwrap(api.GET('/api/v1/printer-alerts/counts')),
  });
  const { query, rows, total } = useCursorList<Schemas['PrinterAlertOut']>(
    ['printer-alerts', category, active, q],
    (cursor) =>
      unwrap(
        api.GET('/api/v1/printer-alerts', {
          params: {
            query: {
              category: category as 'parts' | 'service_call' | 'jam' | 'consumable' | 'other',
              active: active === '' ? null : active === 'true',
              q: q || null,
              limit: PAGE_SIZE,
              cursor,
            },
          },
        }),
      ),
  );
  return (
    <div className="space-y-3">
      <PageHeader
        title="Alertas da impressora"
        subtitle="Registrados pela própria impressora, mais recentes primeiro"
      />
      <Card className="flex flex-wrap items-center gap-3 p-3">
        <Tabs value={category} onValueChange={setCategory}>
          <TabsList>
            {CATEGORIES.map(([c, label]) => {
              const n = counts.data ? counts.data[c as keyof typeof counts.data] : 0;
              return (
                <TabsTrigger key={c} value={c}>
                  {label}
                  {n ? ` (${fmtInt(n)})` : ''}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </Tabs>
        <Select
          aria-label="Situação"
          className="w-40"
          value={active}
          onChange={(e) => {
            setActive(e.target.value);
          }}
        >
          <option value="true">Ativos</option>
          <option value="false">Encerrados</option>
          <option value="">Todos</option>
        </Select>
        <div className="relative min-w-52 flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            aria-hidden
          />
          <Input
            aria-label="Pesquisar alertas da impressora"
            className="pl-8"
            placeholder="Descrição, serial, cliente…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
            }}
          />
        </div>
      </Card>
      <Card className="overflow-x-auto">
        {query.isPending ? (
          <Spinner />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : !rows.length ? (
          <EmptyState title="Nenhum alerta nesta categoria" />
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Apareceu</th>
                <th className="px-3 py-2 text-left">Equipamento</th>
                <th className="px-3 py-2 text-left">Descrição</th>
                <th className="px-3 py-2 text-left">Código</th>
                <th className="px-3 py-2 text-right">Contador</th>
                <th className="px-3 py-2 text-left">Situação</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                  <td className="px-3 py-2 text-xs" title={fmtDateTime(a.first_seen_at)}>
                    <RelativeTime value={a.first_seen_at} />
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <Link to={`/parque/${a.device_id}`} className="font-mono text-brand-600 hover:underline">
                      {a.serial}
                    </Link>
                    <span className="block text-slate-500">
                      {a.model ?? ''} · {a.customer_name}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">{a.description ?? '—'}</td>
                  <td className="px-3 py-2 text-xs">
                    {a.code}
                    <span className="block text-slate-500">
                      {SEVERITY[a.severity] ?? `severidade ${String(a.severity)}`}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right text-xs tabular-nums">{fmtInt(a.total_at)}</td>
                  <td className="px-3 py-2 text-xs">
                    {a.cleared_at ? (
                      <span title={fmtDateTime(a.cleared_at)}>
                        encerrado <RelativeTime value={a.cleared_at} />
                      </span>
                    ) : (
                      <Badge tone="red">ativo</Badge>
                    )}
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
