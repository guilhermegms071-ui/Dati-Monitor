import { describeForecast } from './forecast';

const now = new Date('2026-09-29T15:00:00Z');

describe('previsão de toner', () => {
  it('mostra janela, datas, páginas, método e confiança', () => {
    const f = describeForecast(
      {
        days_to_empty: '12.0',
        days_to_empty_min: '10.2',
        days_to_empty_max: '15.4',
        pages_left: 2000,
        forecast_method: 'regression',
        forecast_confidence: '0.850',
      },
      now,
    );
    expect(f).toEqual({
      summary: 'acaba em 10 a 15 dias (09/10 a 14/10)',
      detail: '~2.000 páginas restantes · regressão linear · confiança 85%',
      uncertain: false,
    });
  });

  it('confiança baixa nunca aparece como certa', () => {
    const f = describeForecast({ days_to_empty: 20, forecast_method: 'direct', forecast_confidence: 0.3 }, now);
    expect(f?.uncertain).toBe(true);
    expect(f?.summary).toBe('estimativa incerta (~20 dias)');
    expect(f?.detail).toBe('consumo por página · confiança 30%');
  });

  it('sem previsão não mostra nada', () => {
    expect(describeForecast({ days_to_empty: null }, now)).toBeNull();
  });
});
