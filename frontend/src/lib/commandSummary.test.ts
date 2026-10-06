import { commandSummary } from './commandSummary';

describe('commandSummary', () => {
  it('resume a varredura', () => {
    const lines = commandSummary('scan_now', {
      new: 0,
      probed: 254,
      ranges: 1,
      removed: 0,
      targets: 254,
      duration_s: 12.42,
      printers_found: 4,
    });
    expect(lines).toEqual([
      '4 impressoras encontradas em 254 endereços testados (1 faixa).',
      'Nenhuma nova (já eram conhecidas pelo coletor).',
      'Duração: 12 s.',
    ]);
    expect(commandSummary('scan_now', { new: 1, probed: 10, ranges: 2, printers_found: 1 })[1]).toBe(
      '1 nova: aparece em Descobertas depois da primeira leitura.',
    );
  });

  it('resume leitura e ping; sem resumo cai no JSON', () => {
    expect(commandSummary('read_now', { ok: 3, failed: 1, devices: [] })).toEqual(['3 equipamentos lidos, 1 falha.']);
    expect(commandSummary('ping_host', { ip: '10.0.0.9', reachable: false })).toEqual(['10.0.0.9: não respondeu.']);
    expect(commandSummary('diagnostics', { clock: {} })).toEqual([]);
    expect(commandSummary('scan_now', {})).toEqual([]);
  });
});
