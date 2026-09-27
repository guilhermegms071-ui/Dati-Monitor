import { QueryClient } from '@tanstack/react-query';

import { SSEParser, applyEvent } from './live';

function collect(chunks: string[]): [string, string][] {
  const out: [string, string][] = [];
  const p = new SSEParser();
  for (const c of chunks) p.feed(c, (e, d) => out.push([e, d]));
  return out;
}

describe('SSEParser', () => {
  it('monta eventos que chegam quebrados em vários pedaços', () => {
    expect(collect(['event: ag', 'ent\ndata: {"id":', '"a1"}\n', '\n'])).toEqual([['agent', '{"id":"a1"}']]);
  });

  it('aceita CRLF, junta várias linhas data e ignora comentários de keep-alive', () => {
    expect(collect([': ping\r\n\r\nevent: command\r\ndata: a\r\ndata: b\r\n\r\n'])).toEqual([['command', 'a\nb']]);
  });

  it('volta para o tipo "message" depois de cada evento', () => {
    expect(collect(['event: devices\ndata: 1\n\ndata: 2\n\n'])).toEqual([
      ['devices', '1'],
      ['message', '2'],
    ]);
  });
});

describe('applyEvent', () => {
  it('invalida só as consultas do que mudou', () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, 'invalidateQueries').mockResolvedValue(undefined);
    const devices = vi.fn();

    applyEvent(qc, 'command', { type: 'command', id: 'c1', agent_id: 'a1' }, devices);
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([
      ['commands', 'a1'],
      ['command', 'c1'],
    ]);

    spy.mockClear();
    applyEvent(qc, 'agent', { type: 'agent', id: 'a1' }, devices);
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([
      ['agents'],
      ['agent', 'a1'],
      ['cluster'],
      ['dashboard'],
    ]);

    spy.mockClear();
    applyEvent(qc, 'devices', { type: 'devices' }, devices);
    expect(devices).toHaveBeenCalledOnce();
    expect(spy).not.toHaveBeenCalled();

    applyEvent(qc, 'resync', { type: 'resync' }, devices);
    expect(spy).toHaveBeenCalledWith();
  });
});
