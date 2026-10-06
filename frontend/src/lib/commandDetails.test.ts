import { detailRows, deviceRows } from './commandDetails';

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
