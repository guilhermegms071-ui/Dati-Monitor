import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download } from 'lucide-react';
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

import { DeviceStatus, SupplyBars } from '../../components/domain';
import { Button } from '../../components/ui/button';
import { Dialog } from '../../components/ui/dialog';
import { Field, Input, Select, Textarea } from '../../components/ui/form';
import {
  Badge,
  Card,
  CardHeader,
  Checkbox,
  EmptyState,
  ErrorState,
  KeyValue,
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
import { dayKey, fmtCommunication, fmtDate, fmtDateTime, fmtInt, fmtPercent } from '../../lib/format';
import { DEVICE_EVENT } from '../../lib/labels';
import { showError, showSuccess } from '../../lib/notify';

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
    <div className="space-y-4">
      <PageHeader
        title={`${d.brand ?? ''} ${d.model ?? d.serial}`.trim()}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{d.serial}</span>· {d.ip ?? 'sem IP'} · {d.customer_name} / {d.site_name}
            <DeviceStatus status={d.last_status} disconnected={d.disconnected} />
            {!d.active ? <Badge>Desativado</Badge> : null}
          </span>
        }
        actions={
          d.last_agent_id && can('agents.command') ? <DeviceActions device={d} agentId={d.last_agent_id} /> : null
        }
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4 lg:col-span-2">
          <KeyValue
            items={[
              ['Comunicação', fmtCommunication(d.last_read_at)],
              ['Descoberta', fmtDate(d.first_seen_at)],
              [
                'Total',
                <span className="text-lg font-semibold" key="t">
                  {fmtInt(d.last_total)}
                </span>,
              ],
              ['PB / Cor', `${fmtInt(d.last_mono)} / ${fmtInt(d.last_color)}`],
              ['Coletor (DCA)', d.agent_name ?? '—'],
              ['Perfil / fonte', `${d.profile_key ?? '—'} / ${d.counter_source ?? '—'}`],
              ['MAC', d.mac ?? '—'],
              ['Firmware', d.firmware ?? '—'],
              ['Painel', d.last_panel_text ?? '—'],
              ['Erros', d.last_error_reasons.length ? d.last_error_reasons.join(', ') : 'nenhum'],
            ]}
          />
        </Card>
        <Card>
          <CardHeader title="Níveis" />
          <div className="p-4">
            <SupplyBars supplies={d.supplies} />
          </div>
        </Card>
      </div>
      <Tabs defaultValue="counters">
        <TabsList>
          <TabsTrigger value="counters">Contadores</TabsTrigger>
          <TabsTrigger value="readings">Leituras</TabsTrigger>
          <TabsTrigger value="supplies">Suprimentos</TabsTrigger>
          <TabsTrigger value="events">Eventos</TabsTrigger>
          <TabsTrigger value="adjustments">Ajustes</TabsTrigger>
          <TabsTrigger value="data">Dados cadastrais</TabsTrigger>
        </TabsList>
        <TabsContent value="counters">
          <CountersChart deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="readings">
          <ReadingsTable deviceId={deviceId} canAdjust={can('readings.adjust')} />
        </TabsContent>
        <TabsContent value="supplies">
          <SuppliesTab deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="events">
          <EventsTab deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="adjustments">
          <AdjustmentsTab deviceId={deviceId} />
        </TabsContent>
        <TabsContent value="data">
          <DeviceForm
            key={JSON.stringify([d.id, d.asset_tag, d.sector, d.notes, d.monitored, d.active])}
            device={d}
            editable={can('devices.write')}
          />
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
      <Button
        size="sm"
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
        Ler agora
      </Button>
      {target ? (
        <>
          <Button size="sm" variant="secondary" onClick={() => void send('snmp_test', target)}>
            Testar SNMP
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void send('read_device', target)}>
            Leitura bruta
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void send('mib_walk', target)}>
            Walk
          </Button>
        </>
      ) : null}
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
      <div className="h-72 p-3">
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
              <Bar dataKey="PB" stackId="p" fill="#334155" />
              <Bar dataKey="Cor" stackId="p" fill="#0ea5e9" />
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
                  <td className="px-3 py-1.5 text-right text-xs text-slate-500">
                    {s.days_to_empty !== null ? `acaba em ~${String(Math.round(Number(s.days_to_empty)))} dias` : ''}
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
  const q = useQuery({
    queryKey: ['device', deviceId, 'events'],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/devices/{device_id}/events', {
          params: { path: { device_id: deviceId }, query: { limit: 200 } },
        }),
      ),
  });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data.items.length) return <EmptyState title="Nenhum evento" />;
  return (
    <Card className="p-4">
      <ol className="relative space-y-4 border-l border-slate-200 pl-5 dark:border-slate-700">
        {q.data.items.map((e) => (
          <li key={e.id}>
            <span className="absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full bg-brand-500" aria-hidden />
            <p className="text-sm font-medium">{DEVICE_EVENT[e.type] ?? e.type}</p>
            <p className="text-xs text-slate-500">
              <RelativeTime value={e.created_at} />
            </p>
            {Object.keys(e.data).length ? (
              <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-[11px] dark:bg-slate-800">
                {JSON.stringify(e.data, null, 2)}
              </pre>
            ) : null}
          </li>
        ))}
      </ol>
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

function DeviceForm({ device, editable }: { device: Row; editable: boolean }) {
  const qc = useQueryClient();
  const [form, setForm] = useState({
    asset_tag: device.asset_tag ?? '',
    sector: device.sector ?? '',
    notes: device.notes ?? '',
    monitored: device.monitored,
    active: device.active,
  });
  const [busy, setBusy] = useState(false);
  return (
    <Card className="p-4">
      <form
        className="grid gap-3 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          unwrap(api.PATCH('/api/v1/devices/{device_id}', { params: { path: { device_id: device.id } }, body: form }))
            .then((row) => {
              qc.setQueryData(['device', device.id], row);
              void qc.invalidateQueries({ queryKey: ['park'] });
              showSuccess('Equipamento atualizado');
            })
            .catch((err: unknown) => {
              showError(err, 'Não foi possível salvar');
            })
            .finally(() => {
              setBusy(false);
            });
        }}
      >
        <Field label="PAT (patrimônio)" htmlFor="d-pat">
          <Input
            id="d-pat"
            disabled={!editable}
            value={form.asset_tag}
            onChange={(e) => {
              setForm({ ...form, asset_tag: e.target.value });
            }}
          />
        </Field>
        <Field label="Setor" htmlFor="d-sector">
          <Input
            id="d-sector"
            disabled={!editable}
            value={form.sector}
            onChange={(e) => {
              setForm({ ...form, sector: e.target.value });
            }}
          />
        </Field>
        <Field label="Observação" htmlFor="d-notes" className="sm:col-span-2">
          <Textarea
            id="d-notes"
            disabled={!editable}
            value={form.notes}
            onChange={(e) => {
              setForm({ ...form, notes: e.target.value });
            }}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={form.monitored}
            onCheckedChange={(v) => {
              if (editable) setForm({ ...form, monitored: v });
            }}
            label="Monitorar"
          />{' '}
          Monitorar (alertas e relatórios)
        </label>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={form.active}
            onCheckedChange={(v) => {
              if (editable) setForm({ ...form, active: v });
            }}
            label="Ativo"
          />{' '}
          Ativo no parque
        </label>
        {editable ? (
          <div className="sm:col-span-2">
            <Button type="submit" loading={busy}>
              Salvar
            </Button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}
