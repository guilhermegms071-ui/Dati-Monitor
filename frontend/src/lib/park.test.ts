import { colorValue, parkStatus, tonerBars } from './park';

const row = (p: Partial<{ active: boolean; disconnected: boolean; last_status: string }>) => ({
  active: true,
  disconnected: false,
  last_status: 'ready',
  ...p,
});

describe('parkStatus', () => {
  it('verde para ok, laranja para Atenção, vermelho para Erro', () => {
    expect(parkStatus(row({}))).toEqual({ label: 'Pronta', tone: 'green' });
    expect(parkStatus(row({ last_status: 'printing' }))).toEqual({ label: 'Imprimindo', tone: 'green' });
    expect(parkStatus(row({ last_status: 'energy_saving' }))).toEqual({ label: 'Economia', tone: 'green' });
    expect(parkStatus(row({ last_status: 'warning' }))).toEqual({ label: 'Atenção', tone: 'orange' });
    expect(parkStatus(row({ last_status: 'error' }))).toEqual({ label: 'Erro', tone: 'red' });
  });
  it('sem conexão (cinza) vence o último status; desativado (cinza-claro) vence tudo', () => {
    expect(parkStatus(row({ disconnected: true, last_status: 'error' }))).toEqual({
      label: 'Sem conexão',
      tone: 'gray',
    });
    expect(parkStatus(row({ last_status: 'offline' }))).toEqual({ label: 'Sem conexão', tone: 'gray' });
    expect(parkStatus(row({ active: false, disconnected: true }))).toEqual({ label: 'Desativado', tone: 'muted' });
    expect(parkStatus(row({ last_status: 'unknown' }))).toEqual({ label: 'Sem leitura', tone: 'gray' });
  });
});

describe('tonerBars', () => {
  it('ordem K/C/M/Y, só cores presentes, vermelho abaixo de 10%', () => {
    const bars = tonerBars([
      { color: 'yellow', percent: 72, level_state: 'ok', description: null },
      { color: 'cyan', percent: 9.9, level_state: 'ok', description: null },
      { color: 'black', percent: 10, level_state: 'ok', description: null },
    ]);
    expect(bars.map((b) => [b.letter, b.percent, b.low])).toEqual([
      ['K', 10, false],
      ['C', 9.9, true],
      ['Y', 72, false],
    ]);
  });
  it('nível desconhecido não é "baixo"; outras cores (resíduo) ficam de fora', () => {
    const bars = tonerBars([
      { color: 'black', percent: null, level_state: 'unknown', description: null },
      { color: 'waste', percent: 5, level_state: 'ok', description: null },
    ]);
    expect(bars).toEqual([{ color: 'black', letter: 'K', percent: null, low: false }]);
  });
});

describe('colorValue', () => {
  it('monocromática é "—" (null), nunca 0; colorida mostra o contador', () => {
    expect(colorValue({ is_color: false, last_color: 0 })).toBeNull();
    expect(colorValue({ is_color: null, last_color: null })).toBeNull();
    expect(colorValue({ is_color: true, last_color: 116881 })).toBe(116881);
  });
});
