/** Previsão de término do toner (seção 16.6): janela otimista/pessimista, páginas restantes, método e
 * confiança. Previsão com confiança baixa nunca aparece como certa. */

export const MIN_CONFIDENCE = 0.5;

export interface ForecastFields {
  days_to_empty: string | number | null;
  days_to_empty_min?: string | number | null;
  days_to_empty_max?: string | number | null;
  pages_left?: number | null;
  forecast_method?: string | null;
  forecast_confidence?: string | number | null;
}

export interface ForecastText {
  summary: string;
  detail: string;
  uncertain: boolean;
}

const METHOD: Record<string, string> = { regression: 'regressão linear', direct: 'consumo por página' };

const num = (v: string | number | null | undefined): number | null =>
  v === null || v === undefined || v === '' ? null : Number(v);

function dateAfter(days: number, now: Date): string {
  const d = new Date(now.getTime() + days * 86_400_000);
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', timeZone: 'America/Sao_Paulo' });
}

export function describeForecast(f: ForecastFields, now: Date = new Date()): ForecastText | null {
  const days = num(f.days_to_empty);
  if (days === null) return null;
  const lo = num(f.days_to_empty_min) ?? days;
  const hi = num(f.days_to_empty_max) ?? days;
  const confidence = num(f.forecast_confidence) ?? 0;
  const uncertain = confidence < MIN_CONFIDENCE;
  const a = Math.max(0, Math.round(lo));
  const b = Math.max(a, Math.round(hi));
  const range = a === b ? `~${String(a)} dias` : `${String(a)} a ${String(b)} dias`;
  const dates = a === b ? dateAfter(a, now) : `${dateAfter(a, now)} a ${dateAfter(b, now)}`;
  const parts = [
    f.pages_left !== null && f.pages_left !== undefined
      ? `~${f.pages_left.toLocaleString('pt-BR')} páginas restantes`
      : null,
    f.forecast_method ? (METHOD[f.forecast_method] ?? f.forecast_method) : null,
    `confiança ${String(Math.round(confidence * 100))}%`,
  ].filter(Boolean);
  return {
    summary: uncertain ? `estimativa incerta (${range})` : `acaba em ${range} (${dates})`,
    detail: parts.join(' · '),
    uncertain,
  };
}
