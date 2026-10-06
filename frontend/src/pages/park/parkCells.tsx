import { TriangleAlert } from 'lucide-react';

import { Tooltip } from '../../components/ui/primitives';
import type { Schemas } from '../../lib/api';
import { fmtDateTime, fmtInt, fmtRelative } from '../../lib/format';
import { parkStatus, tonerBars, type StatusTone } from '../../lib/park';
import { cn } from '../../lib/utils';

type Row = Schemas['ParkRow'];
type Supply = Schemas['SupplyLevel'];

const TONE_CLASS: Record<StatusTone, string> = {
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950 dark:text-emerald-300',
  orange: 'bg-amber-50 text-amber-800 ring-amber-600/25 dark:bg-amber-950 dark:text-amber-300',
  red: 'bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-950 dark:text-red-300',
  gray: 'bg-zinc-100 text-zinc-600 ring-zinc-500/20 dark:bg-zinc-800 dark:text-zinc-300',
  muted: 'bg-zinc-50 text-zinc-400 ring-zinc-400/15 dark:bg-zinc-900 dark:text-zinc-500',
};

const DOT_CLASS: Record<StatusTone, string> = {
  green: 'bg-emerald-500',
  orange: 'bg-amber-500',
  red: 'bg-red-500',
  gray: 'bg-zinc-400',
  muted: 'bg-zinc-300',
};

export function StatusPill({ row }: { row: Pick<Row, 'active' | 'disconnected' | 'last_status'> }) {
  const s = parkStatus(row);
  return (
    <Tooltip content={s.detail}>
      <span
        className={cn(
          'inline-flex w-[112px] items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset',
          TONE_CLASS[s.tone],
        )}
        data-tone={s.tone}
      >
        <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', DOT_CLASS[s.tone])} aria-hidden />
        {s.label}
      </span>
    </Tooltip>
  );
}

/**
 * Medidor do Parque: contador total em destaque e, embaixo, a divisão PB / Cor (monocromática mostra só PB).
 * Sem leitura: "—".
 */
export function Meter({ row }: { row: Pick<Row, 'last_total' | 'last_mono' | 'last_color' | 'is_color'> }) {
  if (row.last_total === null && row.last_mono === null) {
    return <span className="font-mono text-sm text-zinc-400">—</span>;
  }
  const color = row.is_color === false ? null : row.last_color;
  return (
    <span
      className="inline-flex min-w-[164px] flex-col items-center rounded-lg border border-zinc-200 bg-white px-3 py-1 shadow-[0_1px_1px_rgba(16,24,40,0.04)] dark:border-zinc-700 dark:bg-zinc-900"
      data-testid="meter"
    >
      <span className="font-mono text-[15px] font-semibold leading-5 tabular-nums text-zinc-900 dark:text-white">
        {fmtInt(row.last_total)}
      </span>
      {row.last_mono !== null || color !== null ? (
        <span className="flex items-center gap-2.5 whitespace-nowrap font-mono text-[11px] leading-4 tabular-nums text-zinc-500 dark:text-zinc-400">
          <span className="inline-flex items-center gap-1 whitespace-nowrap">
            <span className="h-1.5 w-1.5 rounded-full bg-zinc-700 dark:bg-zinc-300" aria-hidden />
            PB {fmtInt(row.last_mono)}
          </span>
          {color !== null ? (
            <span className="inline-flex items-center gap-1 whitespace-nowrap">
              <span className="h-1.5 w-1.5 rounded-full bg-sky-500" aria-hidden />
              Cor {fmtInt(color)}
            </span>
          ) : null}
        </span>
      ) : null}
    </span>
  );
}

const BAR_FILL: Record<string, string> = {
  black: 'bg-zinc-800 dark:bg-zinc-200',
  cyan: 'bg-cyan-500',
  magenta: 'bg-fuchsia-500',
  yellow: 'bg-yellow-400',
};

export function TonerBars({ supplies }: { supplies: Supply[] }) {
  const bars = tonerBars(supplies);
  if (!bars.length) return <span className="text-zinc-400">—</span>;
  const text = bars
    .map((b) => `${b.letter} ${b.percent === null ? 'n/d' : `${String(Math.round(b.percent))}%`}`)
    .join(' · ');
  return (
    <Tooltip content={text}>
      <span className="flex w-16 flex-col gap-[3px]" aria-label={`Toner: ${text}`} data-testid="toner-bars">
        {bars.map((b) => (
          <span key={b.color} className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
            {b.percent !== null ? (
              <span
                className={cn('block h-full rounded-full', b.low ? 'bg-red-500' : BAR_FILL[b.color])}
                style={{ width: `${String(Math.max(3, Math.min(100, b.percent)))}%` }}
                data-low={b.low ? 'true' : undefined}
              />
            ) : null}
          </span>
        ))}
      </span>
    </Tooltip>
  );
}

/** Número das colunas Total/PB/Cor: à direita, Geist Mono, milhar pt-BR; ausente = "—" cinza. */
export function Num({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="font-mono text-sm text-zinc-400">—</span>;
  return <span className="font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-100">{fmtInt(value)}</span>;
}

export function LastCommunication({ row, highlight }: { row: Row; highlight?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <Tooltip content={fmtDateTime(row.last_read_at)}>
        <span
          className={cn(
            'text-sm',
            row.disconnected ? 'font-medium text-red-600 dark:text-red-400' : 'text-zinc-600 dark:text-zinc-400',
            highlight && 'font-semibold',
          )}
        >
          {row.last_read_at ? fmtRelative(row.last_read_at) : 'nunca'}
        </span>
      </Tooltip>
      {row.comm_unstable ? (
        <Tooltip content="comunicação instável — verificar cabo/porta/duplex">
          <TriangleAlert
            className="h-3.5 w-3.5 text-amber-500"
            aria-label="comunicação instável — verificar cabo/porta/duplex"
            data-testid="comm-unstable"
          />
        </Tooltip>
      ) : null}
    </span>
  );
}
