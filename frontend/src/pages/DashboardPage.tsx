import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Droplet, Printer, Server, WifiOff } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { ReactivateButton } from '../components/Reactivate';
import {
  Card,
  CardHeader,
  EmptyState,
  ErrorState,
  PageHeader,
  RelativeTime,
  Spinner,
} from '../components/ui/primitives';
import { api, unwrap } from '../lib/api';
import { useAuth } from '../lib/auth-context';
import { describeForecast } from '../lib/forecast';
import { fmtDate, fmtInt, fmtPercent } from '../lib/format';
import { SUPPLY_COLOR } from '../lib/labels';

function Stat({
  label,
  value,
  icon,
  tone,
  to,
}: {
  label: string;
  value: number;
  icon: ReactNode;
  tone: string;
  to?: string;
}) {
  const body = (
    <Card className="flex items-center gap-3 p-4 transition-shadow hover:shadow-md">
      <div className={`rounded-lg p-2.5 ${tone}`}>{icon}</div>
      <div>
        <p className="text-2xl font-semibold tabular-nums">{fmtInt(value)}</p>
        <p className="text-xs text-slate-500">{label}</p>
      </div>
    </Card>
  );
  return to ? <Link to={to}>{body}</Link> : body;
}

export function DashboardPage() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ['dashboard'], queryFn: () => unwrap(api.GET('/api/v1/dashboard')) });
  if (q.isPending) return <Spinner />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const d = q.data;
  const chart = d.pages_per_day.map((p) => ({
    dia: fmtDate(p.day).slice(0, 5),
    PB: p.mono,
    Cor: p.color,
    Equipamentos: p.devices,
  }));
  const month = d.month_production;
  return (
    <div className="space-y-4">
      <PageHeader title="Visão geral" subtitle="Situação do parque monitorado" />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Equipamentos monitorados"
          value={d.cards.devices_monitored}
          icon={<Printer className="h-5 w-5" />}
          tone="bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
          to="/parque"
        />
        <Stat
          label="Equipamentos online"
          value={d.cards.devices_online}
          icon={<Printer className="h-5 w-5" />}
          tone="bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          to="/parque"
        />
        <Stat
          label="Desconectados"
          value={d.cards.devices_disconnected}
          icon={<WifiOff className="h-5 w-5" />}
          tone="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
          to="/parque?desconectados=1"
        />
        <Stat
          label="Coletores online"
          value={d.cards.agents_online}
          icon={<Server className="h-5 w-5" />}
          tone="bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          to="/coletores"
        />
        <Stat
          label="Coletores offline"
          value={d.cards.agents_offline}
          icon={<Server className="h-5 w-5" />}
          tone="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
          to="/coletores?estado=offline"
        />
        <Stat
          label="Toners críticos (≤ 10%)"
          value={d.cards.toners_critical}
          icon={<Droplet className="h-5 w-5" />}
          tone="bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        />
      </div>
      {d.cards.alerts_open ? (
        <p className="flex items-center gap-2 rounded-md bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4" /> {fmtInt(d.cards.alerts_open)} alerta(s) aberto(s)
        </p>
      ) : null}

      <Card>
        <CardHeader
          title={`Produção do mês (${month.month.slice(5)}/${month.month.slice(0, 4)})`}
          subtitle={`${fmtInt(month.devices)} equipamento(s) com leitura; regressões de contador não entram`}
        />
        <div className="grid grid-cols-3 gap-3 p-4" data-testid="month-production">
          {(
            [
              ['PB', month.mono],
              ['Cor', month.color],
              ['Total', month.total],
            ] as const
          ).map(([label, v]) => (
            <div key={label}>
              <p className="text-2xl font-semibold tabular-nums">{fmtInt(v)}</p>
              <p className="text-xs text-slate-500">{label}</p>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <CardHeader
          title="Páginas por dia (últimos 30 dias)"
          subtitle="Soma dos contadores de todos os equipamentos, PB × cor"
        />
        <div className="h-72 p-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
              <XAxis dataKey="dia" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => fmtInt(v)} width={70} />
              <ChartTooltip formatter={(v) => fmtInt(Number(v))} />
              <Legend />
              <Bar dataKey="PB" stackId="p" fill="#334155" />
              <Bar dataKey="Cor" stackId="p" fill="#0ea5e9" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card>
        <CardHeader title="Equipamentos comunicando por dia" subtitle="Equipamentos com leitura válida em cada dia" />
        <div className="h-56 p-3" data-testid="devices-per-day">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart}>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
              <XAxis dataKey="dia" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} allowDecimals={false} width={50} />
              <ChartTooltip formatter={(v) => fmtInt(Number(v))} />
              <Bar dataKey="Equipamentos" fill="#22c55e" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Coletores offline agora" />
          {d.offline_agents.length ? (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {d.offline_agents.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                  <div className="min-w-0">
                    <Link to={`/coletores/${a.id}`} className="font-medium hover:underline">
                      {a.name}
                    </Link>
                    <p className="truncate text-xs text-slate-500">
                      {a.customer_name} · {a.site_name} · último sinal <RelativeTime value={a.last_seen_at} />
                    </p>
                  </div>
                  {can('agents.command') ? <ReactivateButton agentId={a.id} agentName={a.name} /> : null}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Todos os coletores estão online" />
          )}
        </Card>
        <Card>
          <CardHeader title="Toners críticos" subtitle="Nível de 10% ou menos" />
          {d.critical_supplies.length ? (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {d.critical_supplies.map((s) => (
                <li
                  key={`${s.device_id}-${s.color ?? ''}-${s.description ?? ''}`}
                  className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm"
                >
                  <div className="min-w-0">
                    <Link to={`/parque/${s.device_id}`} className="font-medium hover:underline">
                      {s.model ?? s.serial}
                    </Link>
                    <p className="truncate text-xs text-slate-500">
                      {s.serial} · {s.customer_name} · {s.description}
                    </p>
                  </div>
                  <span className="flex items-center gap-2 font-semibold text-red-600">
                    <span
                      className={`h-3 w-3 rounded-full ${SUPPLY_COLOR[s.color ?? '']?.bar ?? 'bg-slate-400'}`}
                      aria-hidden
                    />
                    {fmtPercent(s.percent === null ? null : Number(s.percent))}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Nenhum toner crítico" />
          )}
        </Card>
        <Card>
          <CardHeader
            title="Toners que acabam em até 7 dias"
            subtitle="Pela previsão de consumo (só previsões confiáveis)"
          />
          {d.ending_7_days.length ? (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800">
              {d.ending_7_days.map((s) => {
                const f = describeForecast(s);
                return (
                  <li
                    key={`${s.device_id}-${s.color ?? ''}-${s.description ?? ''}`}
                    className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm"
                  >
                    <div className="min-w-0">
                      <Link to={`/parque/${s.device_id}`} className="font-medium hover:underline">
                        {s.model ?? s.serial}
                      </Link>
                      <p className="truncate text-xs text-slate-500">
                        {s.serial} · {s.customer_name} · {f?.summary}
                      </p>
                    </div>
                    <span className="flex items-center gap-2 font-semibold">
                      <span
                        className={`h-3 w-3 rounded-full ${SUPPLY_COLOR[s.color ?? '']?.bar ?? 'bg-slate-400'}`}
                        aria-hidden
                      />
                      {fmtPercent(s.percent === null ? null : Number(s.percent))}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState title="Nenhum toner acaba nos próximos 7 dias" />
          )}
        </Card>
        <Card>
          <CardHeader title="Previstos para acabar em 30 dias" subtitle="Quantidade de toners por cor" />
          <div className="grid grid-cols-4 gap-2 p-4">
            {(['black', 'cyan', 'magenta', 'yellow'] as const).map((c) => (
              <div key={c} className="rounded-md border border-slate-200 p-3 text-center dark:border-slate-800">
                <span className={`mx-auto mb-1 block h-3 w-3 rounded-full ${SUPPLY_COLOR[c]?.bar ?? ''}`} aria-hidden />
                <p className="text-2xl font-semibold tabular-nums">{fmtInt(d.ending_30_days_by_color[c])}</p>
                <p className="text-xs text-slate-500">{TONER_LABEL[c]}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

const TONER_LABEL: Record<string, string> = { black: 'Preto', cyan: 'Ciano', magenta: 'Magenta', yellow: 'Amarelo' };
