import {
  dayKey,
  fmtBytes,
  fmtCommunication,
  fmtDate,
  fmtDateTime,
  fmtDayTime,
  fmtInt,
  fmtPercent,
  fmtRelative,
  fmtTime,
} from './format';

describe('formatação pt-BR no fuso de São Paulo', () => {
  it('converte UTC para America/Sao_Paulo (UTC-3)', () => {
    expect(fmtDateTime('2026-09-27T02:30:00Z')).toBe('26/09/2026, 23:30');
    expect(dayKey('2026-09-27T02:30:00Z')).toBe('2026-09-26');
    expect(dayKey('2026-09-27T03:00:00Z')).toBe('2026-09-27');
  });

  it('eixos de gráfico: hora e dia/hora completos (sem cortar o minuto)', () => {
    const t = Date.parse('2026-09-27T06:05:00Z');
    expect(fmtTime(t)).toBe('03:05');
    expect(fmtDayTime(t)).toBe('27/09 03:05');
  });

  it('mostra "Hoje"/"Ontem" pelo dia de São Paulo, não pelo dia UTC', () => {
    const now = new Date('2026-09-27T15:00:00Z'); // 12:00 em SP
    expect(fmtCommunication('2026-09-27T11:14:00Z', now)).toBe('Hoje às 08:14');
    expect(fmtCommunication('2026-09-27T02:00:00Z', now)).toBe('Ontem às 23:00');
    expect(fmtCommunication('2026-09-25T12:00:00Z', now)).toBe('25/09/2026, 09:00');
    expect(fmtCommunication(null, now)).toBe('Nunca');
  });

  it('números, porcentagens e tamanhos', () => {
    expect(fmtInt(1234567)).toBe('1.234.567');
    expect(fmtInt(null)).toBe('—');
    expect(fmtPercent(null)).toBe('n/d');
    expect(fmtPercent(7.6)).toBe('8%');
    expect(fmtBytes(1536)).toBe('1,5 KB');
  });

  it('tempo relativo abreviado', () => {
    const now = new Date('2026-09-27T15:00:00Z');
    expect(fmtRelative('2026-09-27T14:59:40Z', now)).toBe('agora');
    expect(fmtRelative('2026-09-27T14:55:00Z', now)).toBe('há 5 min');
    expect(fmtRelative('2026-09-27T13:00:00Z', now)).toBe('há 2 h');
    expect(fmtRelative(null, now)).toBe('nunca');
  });
});

describe('fmtDate com data sem hora', () => {
  it('não muda o dia por causa do fuso', () => {
    expect(fmtDate('2026-09-10')).toBe('10/09/2026');
  });
});
