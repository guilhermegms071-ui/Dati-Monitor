import type { Schemas } from './api';

type Row = Schemas['ParkRow'];
type Supply = Schemas['SupplyLevel'];

export type StatusTone = 'green' | 'orange' | 'red' | 'gray' | 'muted';

/** Toner abaixo disto fica vermelho (mesmo limite da aba "Com alerta" no servidor). */
export const LOW_TONER_PERCENT = 10;

const OK_LABEL: Record<string, string> = {
  ready: 'Pronta',
  printing: 'Imprimindo',
  warmup: 'Aquecendo',
  energy_saving: 'Economia',
};

/** Selo do Parque: verde = ok · laranja = Atenção · vermelho = Erro · cinza = Sem conexão · claro = Desativado. */
export function parkStatus(r: Pick<Row, 'active' | 'disconnected' | 'last_status'>): {
  label: string;
  tone: StatusTone;
} {
  if (!r.active) return { label: 'Desativado', tone: 'muted' };
  if (r.disconnected || r.last_status === 'offline') return { label: 'Sem conexão', tone: 'gray' };
  if (r.last_status === 'error') return { label: 'Erro', tone: 'red' };
  if (r.last_status === 'warning') return { label: 'Atenção', tone: 'orange' };
  const ok = OK_LABEL[r.last_status];
  return ok ? { label: ok, tone: 'green' } : { label: 'Sem leitura', tone: 'gray' };
}

const BAR_ORDER = ['black', 'cyan', 'magenta', 'yellow'] as const;
const BAR_LETTER: Record<string, string> = { black: 'K', cyan: 'C', magenta: 'M', yellow: 'Y' };

export interface TonerBar {
  color: string;
  letter: string;
  percent: number | null;
  low: boolean;
}

/** Barras na ordem K/C/M/Y, só das cores que a impressora tem; `low` = abaixo de LOW_TONER_PERCENT. */
export function tonerBars(supplies: Supply[]): TonerBar[] {
  return BAR_ORDER.flatMap((color) => {
    const s = supplies.find((x) => x.color === color);
    if (!s) return [];
    const percent = s.percent ?? null;
    return [
      { color, letter: BAR_LETTER[color] ?? color, percent, low: percent !== null && percent < LOW_TONER_PERCENT },
    ];
  });
}

/** Cor de uma impressora monocromática é "—" (null), nunca 0. */
export function colorValue(r: Pick<Row, 'is_color' | 'last_color'>): number | null {
  if (r.is_color === false) return null;
  return r.last_color ?? null;
}
