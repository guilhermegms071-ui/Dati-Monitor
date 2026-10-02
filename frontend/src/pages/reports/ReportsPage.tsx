import { useQuery } from '@tanstack/react-query';
import { Download, FileText } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import { CustomerPicker, SitePicker } from '../../components/pickers';
import { Button } from '../../components/ui/button';
import { Field, Input, Select } from '../../components/ui/form';
import { Card, CardHeader, EmptyState, ErrorState, PageHeader, Spinner } from '../../components/ui/primitives';
import { api, downloadFile, unwrap, type Schemas } from '../../lib/api';
import { fmtInt } from '../../lib/format';
import { showError } from '../../lib/notify';
import { NUMERIC, cellText } from '../../lib/reports';
import { cn } from '../../lib/utils';

type Info = Schemas['ReportInfo'];
const PAGE = 100;
const SERIES_COLORS = ['#334155', '#0ea5e9', '#22c55e', '#f59e0b'];

function isoDay(d: Date): string {
  return `${String(d.getFullYear())}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

interface Filters {
  date_from: string;
  date_to: string;
  customer_id: string;
  site_id: string;
  date_type: string;
  group_by: string;
  month: string;
  hours: string;
}

function defaults(info: Info | undefined): Filters {
  const today = new Date();
  const from = new Date(today);
  from.setDate(from.getDate() - ((info?.default_days ?? 30) - 1));
  const prevMonth = new Date(today.getFullYear(), today.getMonth() - 1, 1);
  return {
    date_from: info?.period ? isoDay(from) : '',
    date_to: info?.period || info?.cutoff_date ? isoDay(today) : '',
    customer_id: '',
    site_id: '',
    date_type: info?.date_types[0]?.value ?? '',
    group_by: info?.group_by[0]?.value ?? '',
    month: info?.month ? isoDay(prevMonth).slice(0, 7) : '',
    hours: info?.hours ? '24' : '',
  };
}

function toQuery(f: Filters): Record<string, string> {
  return Object.fromEntries(Object.entries(f).filter(([, v]) => v !== ''));
}

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const catalog = useQuery({ queryKey: ['reports'], queryFn: () => unwrap(api.GET('/api/v1/reports')) });
  const key = params.get('r') ?? 'production';
  const info = catalog.data?.find((r) => r.key === key);
  const groups = useMemo(() => {
    const out = new Map<string, Info[]>();
    for (const r of catalog.data ?? []) out.set(r.group, [...(out.get(r.group) ?? []), r]);
    return [...out.entries()];
  }, [catalog.data]);
  if (catalog.isPending) return <Spinner />;
  if (catalog.isError) return <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />;
  return (
    <div className="space-y-4">
      <PageHeader title="Relatórios" subtitle="Na tela e em CSV, XLSX ou PDF, sempre com os mesmos filtros." />
      <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
        <Card className="h-fit p-2">
          <nav aria-label="Relatórios">
            {groups.map(([group, items]) => (
              <div key={group} className="mb-2">
                <p className="px-2 py-1 text-xs font-semibold uppercase text-slate-500">{group}</p>
                {items.map((r) => (
                  <button
                    key={r.key}
                    type="button"
                    title={r.description}
                    onClick={() => {
                      setParams({ r: r.key });
                    }}
                    className={cn(
                      'block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800',
                      r.key === key && 'bg-brand-50 font-medium text-brand-700 dark:bg-slate-800 dark:text-brand-300',
                    )}
                  >
                    {r.title}
                  </button>
                ))}
              </div>
            ))}
          </nav>
        </Card>
        {info ? <ReportView key={info.key} info={info} /> : <EmptyState title="Relatório não encontrado" />}
      </div>
    </div>
  );
}

function ReportView({ info }: { info: Info }) {
  const [draft, setDraft] = useState<Filters>(() => defaults(info));
  const [applied, setApplied] = useState<Filters>(() => defaults(info));
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState<string | null>(null);
  const query = toQuery(applied);
  const q = useQuery({
    queryKey: ['report', info.key, query, offset],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/reports/{key}', {
          params: { path: { key: info.key }, query: { ...query, offset, limit: PAGE } },
        }),
      ),
  });
  const set = (k: keyof Filters, v: string) => {
    setDraft((d) => ({ ...d, [k]: v, ...(k === 'customer_id' ? { site_id: '' } : {}) }));
  };
  const exportAs = (format: 'csv' | 'xlsx' | 'pdf') => {
    setExporting(format);
    const qs = new URLSearchParams({ ...query, format }).toString();
    downloadFile(`/api/v1/reports/${info.key}/export?${qs}`, `${info.key}.${format}`)
      .catch((err: unknown) => {
        showError(err, 'Exportação falhou');
      })
      .finally(() => {
        setExporting(null);
      });
  };
  return (
    <div className="min-w-0 space-y-4">
      <Card>
        <CardHeader
          title={info.title}
          subtitle={info.description}
          actions={
            <div className="flex gap-1">
              {(['csv', 'xlsx', 'pdf'] as const).map((f) => (
                <Button
                  key={f}
                  size="sm"
                  variant="secondary"
                  loading={exporting === f}
                  onClick={() => {
                    exportAs(f);
                  }}
                >
                  {f === 'pdf' ? <FileText className="h-3.5 w-3.5" /> : <Download className="h-3.5 w-3.5" />}{' '}
                  {f.toUpperCase()}
                </Button>
              ))}
            </div>
          }
        />
        <form
          className="grid gap-3 p-3 sm:grid-cols-2 xl:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            setApplied(draft);
          }}
        >
          {info.period ? (
            <>
              <Field label="De" htmlFor="r-from">
                <Input
                  id="r-from"
                  type="date"
                  value={draft.date_from}
                  onChange={(e) => {
                    set('date_from', e.target.value);
                  }}
                />
              </Field>
              <Field label="Até" htmlFor="r-to">
                <Input
                  id="r-to"
                  type="date"
                  value={draft.date_to}
                  onChange={(e) => {
                    set('date_to', e.target.value);
                  }}
                />
              </Field>
            </>
          ) : null}
          {info.cutoff_date ? (
            <Field label="Data de corte" htmlFor="r-cut">
              <Input
                id="r-cut"
                type="date"
                value={draft.date_to}
                onChange={(e) => {
                  set('date_to', e.target.value);
                }}
              />
            </Field>
          ) : null}
          {info.month ? (
            <Field label="Mês" htmlFor="r-month">
              <Input
                id="r-month"
                type="month"
                value={draft.month}
                onChange={(e) => {
                  set('month', e.target.value);
                }}
              />
            </Field>
          ) : null}
          {info.hours ? (
            <Field label="Sem leitura há mais de (horas)" htmlFor="r-hours">
              <Input
                id="r-hours"
                inputMode="numeric"
                value={draft.hours}
                onChange={(e) => {
                  set('hours', e.target.value.replace(/\D/g, ''));
                }}
              />
            </Field>
          ) : null}
          {info.date_types.length ? (
            <Field label="Tipo de data" htmlFor="r-dtype">
              <Select
                id="r-dtype"
                value={draft.date_type}
                onChange={(e) => {
                  set('date_type', e.target.value);
                }}
              >
                {info.date_types.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {info.group_by.length ? (
            <Field label="Agrupar" htmlFor="r-group">
              <Select
                id="r-group"
                value={draft.group_by}
                onChange={(e) => {
                  set('group_by', e.target.value);
                }}
              >
                {info.group_by.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          <Field label="Cliente" htmlFor="r-customer">
            <CustomerPicker
              id="r-customer"
              value={draft.customer_id}
              onChange={(v) => {
                set('customer_id', v);
              }}
            />
          </Field>
          {draft.customer_id ? (
            <Field label="Local" htmlFor="r-site">
              <SitePicker
                id="r-site"
                customerId={draft.customer_id}
                value={draft.site_id}
                onChange={(v) => {
                  set('site_id', v);
                }}
              />
            </Field>
          ) : null}
          <div className="flex items-end">
            <Button type="submit">Gerar</Button>
          </div>
        </form>
      </Card>
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <ReportResultView result={q.data} offset={offset} onOffset={setOffset} />
      )}
    </div>
  );
}

function ReportResultView({
  result,
  offset,
  onOffset,
}: {
  result: Schemas['ReportResult'];
  offset: number;
  onOffset: (n: number) => void;
}) {
  const { columns, rows, totals, chart, chart_rows: chartRows } = result;
  const xCol = chart ? columns.find((c) => c.key === chart.x) : undefined;
  return (
    <>
      {result.notes.length ? (
        <ul className="space-y-1 text-xs text-slate-500">
          {result.notes.map((n) => (
            <li key={n}>• {n}</li>
          ))}
        </ul>
      ) : null}
      {chart && chartRows && chartRows.length > 1 ? (
        <Card>
          <div className="h-72 p-3" data-testid="report-chart">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chartRows.map((r) => ({ ...r, _x: xCol ? cellText(xCol, r[chart.x]).slice(0, 5) : r[chart.x] }))}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
                <XAxis dataKey="_x" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => fmtInt(v)} width={60} />
                <Tooltip formatter={(v) => fmtInt(Number(v))} />
                <Legend />
                {chart.series.map((s, i) => (
                  <Bar
                    key={s.value}
                    dataKey={s.value}
                    name={s.label}
                    stackId={chart.stacked ? 's' : undefined}
                    fill={SERIES_COLORS[i % SERIES_COLORS.length]}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      ) : null}
      <Card>
        {rows.length === 0 ? (
          <EmptyState title="Nenhum registro com esses filtros" />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm" data-testid="report-table">
              <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
                <tr>
                  {columns.map((c) => (
                    <th
                      key={c.key}
                      className={cn('whitespace-nowrap px-3 py-2', NUMERIC.has(c.kind) ? 'text-right' : 'text-left')}
                    >
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-t border-slate-100 dark:border-slate-800">
                    {columns.map((c) => (
                      <td key={c.key} className={cn('px-3 py-1.5', NUMERIC.has(c.kind) && 'text-right tabular-nums')}>
                        {cellText(c, r[c.key])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              {totals ? (
                <tfoot>
                  <tr className="border-t-2 border-slate-200 font-semibold dark:border-slate-700">
                    {columns.map((c, i) => (
                      <td key={c.key} className={cn('px-3 py-1.5', NUMERIC.has(c.kind) && 'text-right tabular-nums')}>
                        {i === 0
                          ? 'Total'
                          : totals[c.key] === null || totals[c.key] === undefined
                            ? ''
                            : cellText(c, totals[c.key])}
                      </td>
                    ))}
                  </tr>
                </tfoot>
              ) : null}
            </table>
          </div>
        )}
        <div className="flex items-center justify-between border-t border-slate-200 px-3 py-2 text-xs text-slate-500 dark:border-slate-800">
          <span>
            {fmtInt(result.total_rows === 0 ? 0 : offset + 1)}–
            {fmtInt(Math.min(offset + rows.length, result.total_rows))} de {fmtInt(result.total_rows)}
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={offset === 0}
              onClick={() => {
                onOffset(Math.max(0, offset - PAGE));
              }}
            >
              Anteriores
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={offset + PAGE >= result.total_rows}
              onClick={() => {
                onOffset(offset + PAGE);
              }}
            >
              Próximos
            </Button>
          </div>
        </div>
      </Card>
    </>
  );
}
