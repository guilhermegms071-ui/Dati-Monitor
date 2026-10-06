import type { Schemas } from './api';

type Row = Schemas['ParkRow'];
type Supply = Schemas['SupplyLevel'];

export type StatusTone = 'green' | 'orange' | 'red' | 'gray' | 'muted';

/** Toner abaixo disto fica vermelho (mesmo limite da aba "Com alerta" no servidor). */
export const LOW_TONER_PERCENT = 10;

/** Situações em que a impressora está respondendo normalmente: aparecem como "Online" (o detalhe vai na dica). */
const ONLINE_DETAIL: Record<string, string> = {
  ready: 'Pronta para imprimir',
  printing: 'Imprimindo',
  warmup: 'Aquecendo',
  energy_saving: 'Em economia de energia',
};

/**
 * Selo do Parque: verde = Online · laranja = Atenção · vermelho = Erro · cinza = Sem conexão · claro = Desativado.
 * `detail` explica o selo (dica ao passar o mouse).
 */
export function parkStatus(r: Pick<Row, 'active' | 'disconnected' | 'last_status'>): {
  label: string;
  tone: StatusTone;
  detail: string;
} {
  if (!r.active) return { label: 'Desativado', tone: 'muted', detail: 'Equipamento desativado no cadastro' };
  if (r.disconnected || r.last_status === 'offline') {
    return { label: 'Sem conexão', tone: 'gray', detail: 'O coletor não está conseguindo ler esta impressora' };
  }
  if (r.last_status === 'error') return { label: 'Erro', tone: 'red', detail: 'A impressora informou um erro' };
  if (r.last_status === 'warning') return { label: 'Atenção', tone: 'orange', detail: 'A impressora pede atenção' };
  const ok = ONLINE_DETAIL[r.last_status];
  return ok
    ? { label: 'Online', tone: 'green', detail: ok }
    : { label: 'Sem leitura', tone: 'gray', detail: 'Ainda não há leitura desta impressora' };
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
