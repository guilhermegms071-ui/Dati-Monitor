/** Limiares de toner por cor (seção 16.5). */

export const TONER_COLORS = [
  ['black', 'Preto'],
  ['cyan', 'Ciano'],
  ['magenta', 'Magenta'],
  ['yellow', 'Amarelo'],
] as const;
export type Thresholds = Record<(typeof TONER_COLORS)[number][0], number>;

export function toThresholds(raw: Record<string, unknown> | undefined): Thresholds {
  const v = (k: string) => {
    const x = raw?.[k];
    return typeof x === 'number' ? x : 10;
  };
  return { black: v('black'), cyan: v('cyan'), magenta: v('magenta'), yellow: v('yellow') };
}
