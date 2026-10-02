import { cellText } from './reports';

describe('cellText', () => {
  it('formata conforme o tipo da coluna', () => {
    expect(cellText({ kind: 'int' }, 100150)).toBe('100.150');
    expect(cellText({ kind: 'money' }, 162.5).replace(/\s/g, ' ')).toBe('R$ 162,50');
    expect(cellText({ kind: 'percent' }, 95)).toMatch(/^95(,0)?%$/);
    expect(cellText({ kind: 'bool' }, true)).toBe('Sim');
    expect(cellText({ kind: 'date' }, '2026-09-10')).toBe('10/09/2026');
    expect(cellText({ kind: 'text' }, null)).toBe('—');
    expect(cellText({ kind: 'text' }, 'Sede')).toBe('Sede');
  });
});
