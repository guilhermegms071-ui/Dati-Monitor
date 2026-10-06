import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Download, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { useParams } from 'react-router';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { DataList, DeviceStatus } from '../../components/domain';
import { LoadMore } from '../../components/paging';
import { Button } from '../../components/ui/button';
import { Dialog, Menu, MenuItem } from '../../components/ui/dialog';
import { Field, Input, Select, Textarea } from '../../components/ui/form';
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Spinner,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '../../components/ui/primitives';
import { api, downloadFile, unwrap, type Schemas } from '../../lib/api';
import { useAuth } from '../../lib/auth-context';
import { useSendCommand } from '../../lib/commands';
import { dayKey, fmtDate, fmtDateTime, fmtInt, fmtPercent } from '../../lib/format';
import { printerName } from '../../lib/printers';
import { describeForecast } from '../../lib/forecast';
import { DEVICE_EVENT } from '../../lib/labels';
import { PAGE_SIZE, useCursorList } from '../../lib/paging';
import { showError, showSuccess } from '../../lib/notify';

import { AlertsList } from '../alerts/AlertsList';

import { AttributesCard, DeviceForm, DeviceTonerCard } from './DeviceRegistration';
import { CounterTiles, DetailedCounters, DeviceTechCard, SupplyPanel } from './DeviceOverview';
import { ManualReadingButton } from './ManualReading';
import { WebAccessButton } from './WebAccess';

type Row = Schemas['ParkRow'];

export function DeviceDetailPage() {
  const { deviceId = '' } = useParams();
  const { can } = useAuth();
  const q = useQuery({
    queryKey: ['device', deviceId],
    queryFn: () => unwrap(api.GET('/api/v1/devices/{device_id}/row', { params: { path: { device_id: deviceId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const d = q.data;
  return (
    <div className="space-y-5">
      <PageHeader
        title={printerName(d.brand, d.model) || d.serial}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <DeviceStatus status={d.last_status} disconnected={d.disconnected} />
            {!d.active ? <Badge>Desativado</Badge> : null}
            {d.discovery_state === 'pending' ? <Badge tone="yellow">Pendente em Descobertas</Badge> : null}
            {d.discovery_state === 'discarded' ? <Badge tone="red">Descartado</Badge> : null}
            <span className="font-mono text-slate-700 dark:text-slate-300">{d.serial}</span>
            <span className="text-slate-300 dark:text-slate-600">•</span>
            <span>{d.ip ?? 'sem IP'}</span>
            <span className="text-slate-300 dark:text-slate-600">•</span>
            <span>
              {d.customer_name} <span className="text-slate-400">›</span> {d.site_name}
            </span>
          </span>
        }
        actions={
          <>
            {d.ip && can('devices.web_access') ? <WebAccessButton deviceId={d.id} size="md" /> : null}
            {can('readings.adjust') ? <ManualReadingButton deviceId={d.id} size="md" /> : null}
            {d.last_agent_id && can('agents.command') ? <DeviceActions device={d} agentId={d.last_agent_id} /> : null}
          </>
        }
      />
      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Visão geral</TabsTrigger>
          <TabsTrigger value="readings">Leituras</TabsTrigger>
          <TabsTrigger value="supplies">Suprimentos</TabsTrigger>
          <TabsTrigger value="events">Alertas e eventos</TabsTrigger>
          <TabsTrigger value="data">Cadastro</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="space-y-5">
          <CounterTiles device={d} />
          <div className="grid gap-5 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <DetailedCounters deviceId={d.id} color={d.is_color !== false} />
            </div>
            <SupplyPanel deviceId={d.id} />
          </div>
          <div className="grid gap-5 xl:grid-cols-3">
            <div className="xl:col-span-2">
              <CountersChart deviceId={deviceId} />
            </div>
            <DeviceTechCard device={d} />
          </div>
        </TabsContent>
        <TabsContent value="readings" className="space-y-5">
          <ReadingsTable deviceId={deviceId} canAdjust={can('readings.adjust')} />
          <AdjustmentsTab deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="supplies">
          <SuppliesTab deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="events" className="space-y-5">
          <Card>
            <CardHeader title="Alertas" />
            <div className="p-4">
              <AlertsList deviceId={deviceId} compact />
            </div>
          </Card>
          <EventsTab deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="data">
          <div className="space-y-5">
            <DeviceForm deviceId={d.id} editable={can('devices.update')} />
            {can('supplies.monitor') ? <DeviceTonerCard deviceId={d.id} editable={can('devices.update')} /> : null}
            <AttributesCard deviceId={deviceId} />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function DeviceActions({ device, agentId }: { device: Row; agentId: string }) {
  const qc = useQueryClient();
  const { send, busy, watcher } = useSendCommand(agentId);
  const target = device.ip ? { ip: device.ip, port: device.snmp_port } : null;
  return (
    <>
      {target ? (
        <Menu
          trigger={
            <Button variant="secondary">
              Mais ações <ChevronDown className="h-4 w-4" />
            </Button>
          }
        >
          <MenuItem onSelect={() => void send('snmp_test', target)}>Testar SNMP</MenuItem>
          <MenuItem onSelect={() => void send('read_device', target)}>Leitura bruta</MenuItem>
          <MenuItem onSelect={() => void send('mib_walk', target)}>Walk SNMP</MenuItem>
        </Menu>
      ) : null}
      <Button
        loading={busy}
        onClick={() => {
          void unwrap(api.POST('/api/v1/devices/bulk', { body: { device_ids: [device.id], action: 'read_now' } }))
            .then((r) => {
              showSuccess(r.commands.length ? 'Leitura solicitada ao coletor' : 'O local não tem coletor MASTER');
              void qc.invalidateQueries({ queryKey: ['commands'] });
            })
            .catch((err: unknown) => {
              showError(err, 'Ler agora');
            });
        }}
      >
        <RefreshCw className="h-4 w-4" /> Ler agora
      </Button>
      {watcher}
    </>
  );
}

function CountersChart({ deviceId }: { deviceId: string }) {
  const [granularity, setGranularity] = useState<'day' | 'month'>('day');
  const q = useQuery({
    queryKey: ['device', deviceId, 'counters', granularity],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/devices/{device_id}/counters', {
          params: { path: { device_id: deviceId }, query: { granularity, periods: granularity === 'day' ? 30 : 12 } },
        }),
      ),
  });
  const data = (q.data ?? []).map((p) => ({
    periodo: granularity === 'day' ? fmtDate(p.period).slice(0, 5) : fmtDate(p.period).slice(3),
    PB: p.pages_mono ?? 0,
    Cor: p.pages_color ?? 0,
  }));
  return (
    <Card>
      <CardHeader
        title="Páginas por período"
        subtitle="Produção calculada pela diferença entre as leituras"
        actions={
          <Select
            value={granularity}
            onChange={(e) => {
              setGranularity(e.target.value as 'day' | 'month');
            }}
            aria-label="Período"
            className="h-8 w-32"
          >
            <option value="day">Diário</option>
            <option value="month">Mensal</option>
          </Select>
        }
      />
      <div className="h-72 px-3 py-4">
        {q.isPending ? (
          <Spinner />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : data.length < 2 ? (
          <EmptyState title="Ainda não há leituras suficientes para o gráfico" />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
              <XAxis dataKey="periodo" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => fmtInt(v)} width={60} />
              <ChartTooltip formatter={(v) => fmtInt(Number(v))} />
              <Legend />
              <Bar dataKey="PB" stackId="p" fill="#475569" />
              <Bar dataKey="Cor" stackId="p" fill="#0ea5e9" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </Card>
  );
}

function ReadingsTable({ deviceId, canAdjust }: { deviceId: string; canAdjust: boolean }) {
  const [adjusting, setAdjusting] = useState<Schemas['ReadingOut'] | null>(null);
  const q = useInfiniteQuery({
    queryKey: ['device', deviceId, 'readings'],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET('/api/v1/devices/{device_id}/readings', {
          params: { path: { device_id: deviceId }, query: { limit: 100, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor,
  });
  const rows = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <Card>
      <CardHeader
        title="Leituras"
        subtitle="Leituras nunca são editadas: correções viram ajustes com autor e motivo."
        actions={
          <Button
            size="sm"
            variant="secondary"
            onClick={() => {
              void downloadFile(`/api/v1/devices/${deviceId}/readings/export?format=xlsx`, 'leituras.xlsx').catch(
                (err: unknown) => {
                  showError(err, 'Exportação falhou');
                },
              );
            }}
          >
            <Download className="h-3.5 w-3.5" /> Exportar
          </Button>
        }
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} />
      ) : rows.length === 0 ? (
        <EmptyState title="Nenhuma leitura ainda" />
      ) : (
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
              <tr>
                <th className="px-3 py-2 text-left">Data</th>
                <th className="px-3 py-2 text-right">Total</th>
                <th className="px-3 py-2 text-right">PB</th>
                <th className="px-3 py-2 text-right">Cor</th>
                <th className="px-3 py-2 text-left">Fonte</th>
                <th className="px-3 py-2 text-left">Alertas</th>
                {canAdjust ? <th className="px-3 py-2" /> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-1.5">{fmtDateTime(r.read_at)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(r.total)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(r.mono)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{fmtInt(r.color)}</td>
                  <td className="px-3 py-1.5 text-xs text-slate-500">{r.counter_source ?? r.source}</td>
                  <td className="px-3 py-1.5">
                    {r.flags.map((f) => (
                      <Badge key={String(f)} tone="yellow">
                        {String(f)}
                      </Badge>
                    ))}
                  </td>
                  {canAdjust ? (
                    <td className="px-3 py-1.5 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setAdjusting(r);
                        }}
                      >
                        Ajustar
                      </Button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {q.hasNextPage ? (
            <div className="p-3 text-center">
              <Button
                size="sm"
                variant="secondary"
                loading={q.isFetchingNextPage}
                onClick={() => void q.fetchNextPage()}
              >
                Carregar mais
              </Button>
            </div>
          ) : null}
        </div>
      )}
      {adjusting ? (
        <AdjustDialog
          deviceId={deviceId}
          reading={adjusting}
          onClose={() => {
            setAdjusting(null);
          }}
        />
      ) : null}
    </Card>
  );
}

function AdjustDialog({
  deviceId,
  reading,
  onClose,
}: {
  deviceId: string;
  reading: Schemas['ReadingOut'];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [total, setTotal] = useState(reading.total?.toString() ?? '');
  const [mono, setMono] = useState(reading.mono?.toString() ?? '');
  const [color, setColor] = useState(reading.color?.toString() ?? '');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const num = (v: string) => (v.trim() === '' ? null : Number(v));
  return (
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      title="Ajuste manual de leitura"
      description={`Leitura de ${fmtDateTime(reading.read_at)}. A original continua guardada; o ajuste registra autor e motivo.`}
      footer={
        <Button
          loading={busy}
          disabled={reason.trim().length < 5}
          onClick={() => {
            setBusy(true);
            unwrap(
              api.POST('/api/v1/devices/{device_id}/adjustments', {
                params: { path: { device_id: deviceId } },
                body: {
                  reading_id: reading.id,
                  read_at: reading.read_at,
                  total: num(total),
                  mono: num(mono),
                  color: num(color),
                  reason,
                },
              }),
            )
              .then(() => {
                showSuccess('Ajuste registrado');
                void qc.invalidateQueries({ queryKey: ['device', deviceId] });
                onClose();
              })
              .catch((err: unknown) => {
                showError(err, 'Ajuste não registrado');
              })
              .finally(() => {
                setBusy(false);
              });
          }}
        >
          Registrar ajuste
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Total" htmlFor="a-total">
          <Input
            id="a-total"
            inputMode="numeric"
            value={total}
            onChange={(e) => {
              setTotal(e.target.value.replace(/\D/g, ''));
            }}
          />
        </Field>
        <Field label="PB" htmlFor="a-mono">
          <Input
            id="a-mono"
            inputMode="numeric"
            value={mono}
            onChange={(e) => {
              setMono(e.target.value.replace(/\D/g, ''));
            }}
          />
        </Field>
        <Field label="Cor" htmlFor="a-color">
          <Input
            id="a-color"
            inputMode="numeric"
            value={color}
            onChange={(e) => {
              setColor(e.target.value.replace(/\D/g, ''));
            }}
          />
        </Field>
      </div>
      <Field label="Motivo (obrigatório)" htmlFor="a-reason" className="mt-3">
        <Textarea
          id="a-reason"
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
          }}
        />
      </Field>
    </Dialog>
  );
}

function SuppliesTab({ deviceId }: { deviceId: string }) {
  const current = useQuery({
    queryKey: ['device', deviceId, 'supplies'],
    queryFn: () =>
      unwrap(api.GET('/api/v1/devices/{device_id}/supplies', { params: { path: { device_id: deviceId } } })),
  });
  const history = useQuery({
    queryKey: ['device', deviceId, 'supplies-history'],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/devices/{device_id}/supplies/history', {
          params: { path: { device_id: deviceId }, query: { days: 60 } },
        }),
      ),
  });
  const series = new Map<string, Record<string, number | string>>();
  for (const p of history.data ?? []) {
    if (p.percent === null || !p.color) continue;
    const day = dayKey(p.read_at);
    const row = series.get(day) ?? { dia: fmtDate(p.read_at).slice(0, 5) };
    row[p.color] = Number(p.percent);
    series.set(day, row);
  }
  const chart = [...series.values()];
  const colors: Record<string, string> = { cyan: '#06b6d4', magenta: '#d946ef', yellow: '#eab308', black: '#334155' };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Suprimentos agora" />
        {current.isPending ? (
          <Spinner />
        ) : current.isError ? (
          <ErrorState error={current.error} />
        ) : current.data.length === 0 ? (
          <EmptyState title="A impressora não informou suprimentos" />
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {current.data.map((s) => (
                <tr key={s.supply_key} className="border-t border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-1.5">{s.description ?? s.supply_key}</td>
                  <td className="px-3 py-1.5 text-xs text-slate-500">{s.supply_type}</td>
                  <td className="px-3 py-1.5 text-right font-medium">
                    {fmtPercent(s.percent === null ? null : Number(s.percent))}
                  </td>
                  <td className="px-3 py-1.5 text-right text-xs">
                    <ForecastCell supply={s} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card>
        <CardHeader title="Histórico (60 dias)" />
        <div className="h-64 p-3">
          {chart.length < 2 ? (
            <EmptyState title="Histórico insuficiente" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
                <XAxis dataKey="dia" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
                <ChartTooltip />
                <Legend />
                {Object.entries(colors).map(([c, stroke]) => (
                  <Line key={c} type="monotone" dataKey={c} stroke={stroke} dot={false} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>
    </div>
  );
}

function EventsTab({ deviceId }: { deviceId: string }) {
  const { query: q, rows } = useCursorList<Schemas['DeviceEventOut']>(['device', deviceId, 'events'], (cursor) =>
    unwrap(
      api.GET('/api/v1/devices/{device_id}/events', {
        params: { path: { device_id: deviceId }, query: { limit: PAGE_SIZE, cursor } },
      }),
    ),
  );
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!rows.length) return <EmptyState title="Nenhum evento" />;
  return (
    <Card className="p-4">
      <ol className="relative space-y-4 border-l border-slate-200 pl-5 dark:border-slate-700">
        {rows.map((e) => (
          <li key={e.id}>
            <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-brand-500" aria-hidden />
            <p className="text-sm font-medium">{DEVICE_EVENT[e.type] ?? e.type}</p>
            <p className="text-xs text-slate-500">
              <RelativeTime value={e.created_at} />
            </p>
            {Object.keys(e.data).length ? (
              <div className="mt-1.5 rounded-md bg-slate-50 px-3 py-2 dark:bg-slate-800">
                <DataList data={e.data} compact />
              </div>
            ) : null}
          </li>
        ))}
      </ol>
      <LoadMore query={q} shown={rows.length} />
    </Card>
  );
}

function AdjustmentsTab({ deviceId }: { deviceId: string }) {
  const q = useQuery({
    queryKey: ['device', deviceId, 'adjustments'],
    queryFn: () =>
      unwrap(api.GET('/api/v1/devices/{device_id}/adjustments', { params: { path: { device_id: deviceId } } })),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data.length) return <EmptyState title="Nenhum ajuste manual" />;
  return (
    <Card>
      <table className="w-full text-sm">
        <thead className="bg-slate-50 text-xs text-slate-500 dark:bg-slate-800">
          <tr>
            <th className="px-3 py-2 text-left">Quando</th>
            <th className="px-3 py-2 text-left">Leitura</th>
            <th className="px-3 py-2 text-right">Total / PB / Cor</th>
            <th className="px-3 py-2 text-left">Motivo</th>
            <th className="px-3 py-2 text-left">Autor</th>
          </tr>
        </thead>
        <tbody>
          {q.data.map((a) => (
            <tr key={a.id} className="border-t border-slate-100 dark:border-slate-800">
              <td className="px-3 py-1.5">{fmtDateTime(a.created_at)}</td>
              <td className="px-3 py-1.5">{fmtDateTime(a.read_at)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">
                {fmtInt(a.total)} / {fmtInt(a.mono)} / {fmtInt(a.color)}
              </td>
              <td className="px-3 py-1.5">{a.reason}</td>
              <td className="px-3 py-1.5">{a.user_name ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ForecastCell({ supply }: { supply: Schemas['SupplyOut'] }) {
  const f = describeForecast(supply);
  if (!f) return null;
  return (
    <span className="block">
      <span className={f.uncertain ? 'text-amber-600 dark:text-amber-400' : 'text-slate-700 dark:text-slate-300'}>
        {f.summary}
      </span>
      <span className="block text-[11px] text-slate-500">{f.detail}</span>
    </span>
  );
}
