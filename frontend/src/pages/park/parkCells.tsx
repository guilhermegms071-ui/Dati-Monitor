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

export function StatusPill({ row }: { row: Pick<Row, 'active' | 'disconnected' | 'last_status'> }) {
  const s = parkStatus(row);
  return (
    <span
      className={cn(
        'inline-flex w-[104px] items-center justify-start rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset',
        TONE_CLASS[s.tone],
      )}
      data-tone={s.tone}
    >
      {s.label}
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
