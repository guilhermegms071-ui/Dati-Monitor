import { useQuery } from '@tanstack/react-query';
import { CalendarRange, Layers, Palette, Printer } from 'lucide-react';
import type { ReactNode } from 'react';

import { Card, CardHeader, EmptyState, ErrorState, KeyValue, Spinner } from '../../components/ui/primitives';
import { api, unwrap, type Schemas } from '../../lib/api';
import { describeForecast } from '../../lib/forecast';
import { fmtCommunication, fmtDate, fmtInt, fmtPercent } from '../../lib/format';
import { cn } from '../../lib/utils';

type Row = Schemas['ParkRow'];
type Reading = Schemas['ReadingOut'];

/** Cartão de número grande (estilo painel do PaperCut): rótulo, valor e uma linha de contexto. */
export function StatTile({
  label,
  value,
  hint,
  icon,
  tone = 'slate',
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon: ReactNode;
  tone?: 'slate' | 'brand' | 'sky';
}) {
  const iconTone = {
    slate: 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300',
    brand: 'bg-brand-50 text-brand-700 dark:bg-brand-900/40 dark:text-brand-200',
    sky: 'bg-sky-50 text-sky-700 dark:bg-sky-900/40 dark:text-sky-200',
  }[tone];
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-slate-500 dark:text-slate-400">{label}</p>
        <span className={cn('flex h-9 w-9 items-center justify-center rounded-lg', iconTone)} aria-hidden>
          {icon}
        </span>
      </div>
      <p className="mt-2 font-mono text-[28px] font-semibold leading-none tracking-tight text-slate-900 tabular-nums dark:text-white">
        {value}
      </p>
      {hint ? <p className="mt-2 text-xs text-slate-500">{hint}</p> : null}
    </Card>
  );
}

/** Os quatro números do topo: total, PB, cor e produção do mês. */
export function CounterTiles({ device }: { device: Row }) {
  const month = useQuery({
    queryKey: ['device', device.id, 'counters', 'month', 2],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/devices/{device_id}/counters', {
          params: { path: { device_id: device.id }, query: { granularity: 'month', periods: 2 } },
        }),
      ),
  });
  const points = month.data ?? [];
  const current = points[points.length - 1];
  const previous = points.length > 1 ? points[points.length - 2] : undefined;
  const color = device.is_color !== false;
  const read = device.last_read_at ? `Última leitura: ${fmtCommunication(device.last_read_at)}` : 'Ainda sem leitura';
  return (
    <div className={cn('grid gap-4 sm:grid-cols-2', color ? 'xl:grid-cols-4' : 'xl:grid-cols-3')}>
      <StatTile
        label="Contador total"
        value={fmtInt(device.last_total)}
        hint={read}
        icon={<Printer className="h-4.5 w-4.5" />}
        tone="brand"
      />
      <StatTile
        label="Preto e branco"
        value={fmtInt(device.last_mono)}
        hint="Páginas PB acumuladas"
        icon={<Layers className="h-4.5 w-4.5" />}
      />
      {color ? (
        <StatTile
          label="Colorido"
          value={fmtInt(device.last_color)}
          hint="Páginas coloridas acumuladas"
          icon={<Palette className="h-4.5 w-4.5" />}
          tone="sky"
        />
      ) : null}
      <StatTile
        label="Produção do mês"
        value={month.isPending ? '…' : fmtInt(current?.pages ?? null)}
        hint={
          month.isError
            ? 'Não foi possível calcular'
            : current?.pages === null || current?.pages === undefined
              ? 'Aparece a partir da segunda leitura do mês'
              : previous?.pages !== undefined && previous.pages !== null
                ? `Mês anterior: ${fmtInt(previous.pages)} páginas`
                : 'Páginas impressas desde o início do mês'
        }
        icon={<CalendarRange className="h-4.5 w-4.5" />}
      />
    </div>
  );
}

const DETAIL_ROWS: { label: string; mono: keyof Reading | null; color: keyof Reading | null }[] = [
  { label: 'Impressão', mono: 'print_mono', color: 'print_color' },
  { label: 'Cópia', mono: 'copy_mono', color: 'copy_color' },
  { label: 'Formato grande (A3)', mono: 'mono_large', color: 'color_large' },
  { label: 'Digitalização', mono: 'scan', color: null },
  { label: 'Fax', mono: 'fax', color: null },
];

function num(r: Reading, key: keyof Reading | null): number | null {
  if (!key) return null;
  const v = r[key];
  return typeof v === 'number' ? v : null;
}

/** Contadores separados por função (impressão, cópia, A3...) da última leitura. */
export function DetailedCounters({ deviceId, color }: { deviceId: string; color: boolean }) {
  const q = useQuery({
    queryKey: ['device', deviceId, 'readings', 'latest'],
    queryFn: () =>
      unwrap(
        api.GET('/api/v1/devices/{device_id}/readings', {
          params: { path: { device_id: deviceId }, query: { limit: 1 } },
        }),
      ),
  });
  const latest = q.data?.items[0];
  const rows = latest
    ? DETAIL_ROWS.map((d) => ({ ...d, m: num(latest, d.mono), c: num(latest, d.color) })).filter(
        (d) => d.m !== null || d.c !== null,
      )
    : [];
  return (
    <Card>
      <CardHeader
        title="Contadores detalhados"
        subtitle={latest ? `Leitura de ${fmtCommunication(latest.read_at)}` : 'Da última leitura recebida'}
      />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !latest ? (
        <EmptyState title="Nenhuma leitura recebida ainda" />
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 dark:border-slate-800">
              <th className="px-5 py-2.5 text-left">Contador</th>
              <th className="px-5 py-2.5 text-right">Preto e branco</th>
              {color ? <th className="px-5 py-2.5 text-right">Colorido</th> : null}
              <th className="px-5 py-2.5 text-right">Total</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-b border-slate-100 last:border-0 dark:border-slate-800">
                <td className="px-5 py-3 font-medium text-slate-700 dark:text-slate-200">{r.label}</td>
                <td className="px-5 py-3 text-right font-mono tabular-nums">{fmtInt(r.m)}</td>
                {color ? <td className="px-5 py-3 text-right font-mono tabular-nums">{fmtInt(r.c)}</td> : null}
                <td className="px-5 py-3 text-right font-mono font-semibold tabular-nums">
                  {fmtInt((r.m ?? 0) + (r.c ?? 0))}
                </td>
              </tr>
            ))}
            <tr className="bg-slate-50/70 dark:bg-slate-800/40">
              <td className="px-5 py-3 font-semibold">Geral</td>
              <td className="px-5 py-3 text-right font-mono font-semibold tabular-nums">{fmtInt(latest.mono)}</td>
              {color ? (
                <td className="px-5 py-3 text-right font-mono font-semibold tabular-nums">{fmtInt(latest.color)}</td>
              ) : null}
              <td className="px-5 py-3 text-right font-mono text-base font-bold tabular-nums">
                {fmtInt(latest.total)}
              </td>
            </tr>
          </tbody>
        </table>
      )}
      {latest && !rows.length ? (
        <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500 dark:border-slate-800">
          Este modelo informa só o total e a divisão PB/cor; não separa impressão, cópia e digitalização.
        </p>
      ) : null}
    </Card>
  );
}

const SUPPLY_BAR: Record<string, string> = {
  black: 'bg-slate-800 dark:bg-slate-300',
  cyan: 'bg-cyan-500',
  magenta: 'bg-fuchsia-500',
  yellow: 'bg-yellow-400',
};

const SUPPLY_NAME: Record<string, string> = {
  black: 'Preto',
  cyan: 'Ciano',
  magenta: 'Magenta',
  yellow: 'Amarelo',
};

/** Níveis de toner/suprimentos em barras horizontais, com a previsão de término. */
export function SupplyPanel({ deviceId }: { deviceId: string }) {
  const q = useQuery({
    queryKey: ['device', deviceId, 'supplies'],
    queryFn: () =>
      unwrap(api.GET('/api/v1/devices/{device_id}/supplies', { params: { path: { device_id: deviceId } } })),
  });
  return (
    <Card>
      <CardHeader title="Suprimentos" subtitle="Nível atual e previsão de término" />
      {q.isPending ? (
        <Spinner />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data.length ? (
        <EmptyState title="A impressora não informou suprimentos" />
      ) : (
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {q.data.map((s) => {
            const pct = s.percent === null ? null : Number(s.percent);
            const low = pct !== null && pct <= 10;
            const warn = pct !== null && pct > 10 && pct <= 20;
            const f = describeForecast(s);
            const name = s.color ? (SUPPLY_NAME[s.color] ?? s.description) : s.description;
            return (
              <li key={s.supply_key} className="px-5 py-3">
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate font-medium text-slate-700 dark:text-slate-200">
                    {name ?? s.supply_key}
                    {s.supply_type !== 'toner' ? (
                      <span className="ml-1.5 text-xs font-normal text-slate-400">{s.supply_type}</span>
                    ) : null}
                  </span>
                  <span
                    className={cn(
                      'font-mono font-semibold tabular-nums',
                      low ? 'text-red-600' : warn ? 'text-amber-600' : 'text-slate-900 dark:text-slate-100',
                    )}
                  >
                    {pct === null ? 'n/d' : fmtPercent(pct)}
                  </span>
                </div>
                <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                  {pct !== null ? (
                    <div
                      className={cn('h-full rounded-full', (s.color && SUPPLY_BAR[s.color]) ?? 'bg-brand-500')}
                      style={{ width: `${String(Math.min(Math.max(pct, 2), 100))}%` }}
                    />
                  ) : null}
                </div>
                {f ? (
                  <p
                    className={cn(
                      'mt-1 text-xs',
                      f.uncertain ? 'text-amber-600 dark:text-amber-400' : 'text-slate-500 dark:text-slate-400',
                    )}
                  >
                    {f.summary}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** Dados técnicos (rede, coletor, perfil): ficam num cartão à parte para não poluir os contadores. */
export function DeviceTechCard({ device }: { device: Row }) {
  return (
    <Card>
      <CardHeader title="Informações do equipamento" />
      <div className="px-5 py-2">
        <KeyValue
          columns={1}
          items={[
            ['Comunicação', fmtCommunication(device.last_read_at)],
            ['Coletor', device.agent_name ?? '—'],
            ['IP', device.ip ?? '—'],
            ['MAC', device.mac ?? '—'],
            ['Firmware', device.firmware ?? '—'],
            ['Painel', device.last_panel_text ?? '—'],
            ['Erros', device.last_error_reasons.length ? device.last_error_reasons.join(', ') : 'nenhum'],
            ['Perfil de leitura', device.profile_key ?? '—'],
            ['Descoberta em', fmtDate(device.first_seen_at)],
          ]}
        />
      </div>
    </Card>
  );
}
