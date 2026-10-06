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
    <Card className="h-full p-5 transition-all hover:border-slate-300 hover:shadow-md dark:hover:border-slate-700">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium leading-snug text-slate-500 dark:text-slate-400">{label}</p>
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone}`}>{icon}</span>
      </div>
      <p className="mt-2 font-mono text-[28px] font-semibold leading-none tabular-nums text-slate-900 dark:text-white">
        {fmtInt(value)}
      </p>
    </Card>
  );
  return to ? (
    <Link
      to={to}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-brand-500/30"
    >
      {body}
    </Link>
  ) : (
    body
  );
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
    <div className="space-y-5">
      <PageHeader title="Visão geral" subtitle="Situação do parque monitorado" />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        <Stat
          label="Impressoras monitoradas"
          value={d.cards.devices_monitored}
          icon={<Printer className="h-4.5 w-4.5" />}
          tone="bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200"
          to="/parque"
        />
        <Stat
          label="Impressoras online"
          value={d.cards.devices_online}
          icon={<Printer className="h-4.5 w-4.5" />}
          tone="bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          to="/parque"
        />
        <Stat
          label="Sem conexão"
          value={d.cards.devices_disconnected}
          icon={<WifiOff className="h-4.5 w-4.5" />}
          tone="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
          to="/parque?desconectados=1"
        />
        <Stat
          label="Coletores online"
          value={d.cards.agents_online}
          icon={<Server className="h-4.5 w-4.5" />}
          tone="bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
          to="/coletores"
        />
        <Stat
          label="Coletores offline"
          value={d.cards.agents_offline}
          icon={<Server className="h-4.5 w-4.5" />}
          tone="bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
          to="/coletores?estado=offline"
        />
        <Stat
          label="Toner crítico"
          value={d.cards.toners_critical}
          icon={<Droplet className="h-4.5 w-4.5" />}
          tone="bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        />
      </div>
      {d.cards.alerts_open ? (
        <Link
          to="/alertas"
          className="flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-900 hover:bg-amber-100 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
        >
          <AlertTriangle className="h-4 w-4" /> {fmtInt(d.cards.alerts_open)} alerta(s) aberto(s) — ver alertas
        </Link>
      ) : null}

      <Card>
        <CardHeader
          title={`Produção do mês (${month.month.slice(5)}/${month.month.slice(0, 4)})`}
          subtitle={`${fmtInt(month.devices)} equipamento(s) com leitura; regressões de contador não entram`}
        />
        <div
          className="grid grid-cols-3 divide-x divide-slate-100 dark:divide-slate-800"
          data-testid="month-production"
        >
          {(
            [
              ['Preto e branco', month.mono, 'bg-slate-600'],
              ['Colorido', month.color, 'bg-sky-500'],
              ['Total', month.total, 'bg-brand-600'],
            ] as const
          ).map(([label, v, dot]) => (
            <div key={label} className="px-5 py-4">
              <p className="flex items-center gap-1.5 text-sm text-slate-500">
                <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden /> {label}
              </p>
              <p className="mt-1 font-mono text-2xl font-semibold tabular-nums text-slate-900 dark:text-white">
                {fmtInt(v)}
              </p>
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
              <ChartTooltip formatter={(v) => fmtInt(Number(v))} cursor={{ fill: '#94a3b822' }} />
              <Legend />
              <Bar dataKey="PB" stackId="p" fill="#475569" />
              <Bar dataKey="Cor" stackId="p" fill="#0ea5e9" radius={[3, 3, 0, 0]} />
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
              <ChartTooltip formatter={(v) => fmtInt(Number(v))} cursor={{ fill: '#94a3b822' }} />
              <Bar dataKey="Equipamentos" fill="#1d84e0" radius={[3, 3, 0, 0]} />
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
