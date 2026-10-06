import { dataTables, detailRows, deviceRows, parseLogLine } from './commandDetails';

describe('resultado de comando legível', () => {
  it('lista de equipamentos do Ler agora', () => {
    const rows = deviceRows({
      ok: 2,
      failed: 1,
      devices: [
        { ip: '10.10.10.190', ok: true, port: 161, serial: 'ACC2011022817' },
        { ip: '10.10.10.9', ok: false, port: 161, error: 'sem resposta SNMP' },
      ],
    });
    expect(rows).toEqual([
      { serial: 'ACC2011022817', ip: '10.10.10.190', ok: true, error: null },
      { serial: '—', ip: '10.10.10.9', ok: false, error: 'sem resposta SNMP' },
    ]);
  });

  it('rótulos em português, unidades e objetos aninhados', () => {
    expect(
      detailRows({ reachable: true, rtt_ms: 12.34, https: { ok: false, status: 404 }, devices: [] }, ['devices']),
    ).toEqual([
      { label: 'Responde na rede', value: 'Sim' },
      { label: 'Tempo de resposta', value: '12,3 ms' },
      { label: 'Conexão com o servidor · Resultado', value: 'Falhou' },
      { label: 'Conexão com o servidor · Situação', value: '404' },
    ]);
    expect(detailRows({ free_bytes: 5 * 1024 ** 3 })).toEqual([{ label: 'Espaço livre', value: '5 GB' }]);
  });
});

describe('tabelas e logs', () => {
  it('lista de objetos vira tabela com rótulos em português', () => {
    const t = dataTables({
      items: [
        { serial: 'A1', total: 1500 },
        { serial: 'B2', total: 20, ip: '10.0.0.9' },
      ],
    });
    expect(t).toEqual([
      {
        title: 'Itens',
        columns: [
          { key: 'serial', label: 'Nº de série' },
          { key: 'total', label: 'Total' },
          { key: 'ip', label: 'IP' },
        ],
        rows: [
          ['A1', '1.500', '—'],
          ['B2', '20', '10.0.0.9'],
        ],
      },
    ]);
  });

  it('linha de log do coletor', () => {
    const l = parseLogLine(
      '{"time":"2026-10-06T18:07:00Z","level":"ERROR","msg":"varredura interrompida","erro":"timeout","sondados":12}',
    );
    expect(l.level).toBe('error');
    expect(l.message).toBe('varredura interrompida');
    expect(l.time).toBe('06/10/2026, 15:07:00');
    expect(l.fields).toEqual([
      { label: 'Erro', value: 'timeout' },
      { label: 'Sondados', value: '12' },
    ]);
    expect(parseLogLine('texto solto')).toEqual({ time: null, level: null, message: 'texto solto', fields: [] });
  });
});
