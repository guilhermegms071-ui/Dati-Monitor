/** Formatação pt-BR. Os horários chegam em UTC e são exibidos no fuso de São Paulo (PROMPT seção 0). */

export const TIME_ZONE = 'America/Sao_Paulo';

const intFmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 0 });
const decFmt = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 });
const dateTimeFmt = new Intl.DateTimeFormat('pt-BR', {
  timeZone: TIME_ZONE,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
});
const dateFmt = new Intl.DateTimeFormat('pt-BR', {
  timeZone: TIME_ZONE,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
});
const timeFmt = new Intl.DateTimeFormat('pt-BR', { timeZone: TIME_ZONE, hour: '2-digit', minute: '2-digit' });
const dayKeyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});
const relFmt = new Intl.RelativeTimeFormat('pt-BR', { numeric: 'auto' });

export function fmtInt(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : intFmt.format(n);
}

export function fmtDec(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : decFmt.format(n);
}

export function fmtPercent(n: number | null | undefined): string {
  return n === null || n === undefined ? 'n/d' : `${intFmt.format(n)}%`;
}

function toDate(value: string | Date): Date {
  return typeof value === 'string' ? new Date(value) : value;
}

export function fmtDateTime(value: string | Date | null | undefined): string {
  return value ? dateTimeFmt.format(toDate(value)) : '—';
}

export function fmtDate(value: string | Date | null | undefined): string {
  if (!value) return '—';
  // Data sem hora ("2026-09-10", dia de Brasília vindo da API): não converter de fuso.
  const day = typeof value === 'string' ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) : null;
  return day ? `${day[3] ?? ''}/${day[2] ?? ''}/${day[1] ?? ''}` : dateFmt.format(toDate(value));
}

const dayTimeFmt = new Intl.DateTimeFormat('pt-BR', {
  timeZone: TIME_ZONE,
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
});

/** "08:14" (eixos de gráfico de poucas horas). */
export function fmtTime(value: string | Date | number): string {
  return timeFmt.format(typeof value === 'number' ? new Date(value) : toDate(value));
}

/** "27/09 08:14" (eixos de gráfico de vários dias). */
export function fmtDayTime(value: string | Date | number): string {
  return dayTimeFmt.format(typeof value === 'number' ? new Date(value) : toDate(value)).replace(',', '');
}

/** Chave AAAA-MM-DD do dia em São Paulo. */
export function dayKey(value: string | Date): string {
  return dayKeyFmt.format(toDate(value));
}

/** Coluna "Comunicação" do parque: "Hoje às 08:14", "Ontem às 17:02" ou a data completa. */
export function fmtCommunication(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return 'Nunca';
  const d = new Date(value);
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getTime() - 86_400_000));
  const key = dayKey(d);
  if (key === today) return `Hoje às ${timeFmt.format(d)}`;
  if (key === yesterday) return `Ontem às ${timeFmt.format(d)}`;
  return dateTimeFmt.format(d);
}

/** "há 5 min", "em 2 h", "agora". */
export function fmtRelative(value: string | Date | null | undefined, now: Date = new Date()): string {
  if (!value) return 'nunca';
  const diff = (toDate(value).getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return 'agora';
  if (abs < 3600)
    return relFmt
      .format(Math.round(diff / 60), 'minute')
      .replace('minutos', 'min')
      .replace('minuto', 'min');
  if (abs < 86_400)
    return relFmt
      .format(Math.round(diff / 3600), 'hour')
      .replace('horas', 'h')
      .replace('hora', 'h');
  if (abs < 2_592_000) return relFmt.format(Math.round(diff / 86_400), 'day');
  return fmtDate(value);
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${decFmt.format(v)} ${units[i] ?? 'B'}`;
}
