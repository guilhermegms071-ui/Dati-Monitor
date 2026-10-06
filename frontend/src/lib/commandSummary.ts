/** Resumo em português do resultado de um comando (o JSON completo fica em "Ver detalhes"). */
export function commandSummary(type: string, result: Record<string, unknown>): string[] {
  const n = (k: string): number | null => (typeof result[k] === 'number' ? result[k] : null);
  const plural = (v: number, one: string, many: string) => `${String(v)} ${v === 1 ? one : many}`;
  switch (type) {
    case 'scan_now': {
      const found = n('printers_found');
      if (found === null) return [];
      const lines = [
        `${plural(found, 'impressora encontrada', 'impressoras encontradas')} em ${plural(n('probed') ?? 0, 'endereço testado', 'endereços testados')} (${plural(n('ranges') ?? 0, 'faixa', 'faixas')}).`,
      ];
      const fresh = n('new') ?? 0;
      lines.push(
        fresh > 0
          ? `${plural(fresh, 'nova', 'novas')}: aparece${fresh === 1 ? '' : 'm'} em Descobertas depois da primeira leitura.`
          : 'Nenhuma nova (já eram conhecidas pelo coletor).',
      );
      const removed = n('removed') ?? 0;
      if (removed > 0)
        lines.push(
          `${plural(removed, 'saiu', 'saíram')} das faixas e deixa${removed === 1 ? '' : 'm'} de ser lida${removed === 1 ? '' : 's'}.`,
        );
      const dur = n('duration_s');
      if (dur !== null) lines.push(`Duração: ${String(Math.round(dur))} s.`);
      return lines;
    }
    case 'read_now':
    case 'read_device': {
      const ok = n('ok');
      if (ok === null) return [];
      const failed = n('failed') ?? 0;
      const lines = [
        `${plural(ok, 'equipamento lido', 'equipamentos lidos')}${failed ? `, ${plural(failed, 'falha', 'falhas')}` : ''}.`,
      ];
      const missing = Array.isArray(result.not_found) ? result.not_found.length : 0;
      if (missing)
        lines.push(`${plural(missing, 'equipamento não encontrado', 'equipamentos não encontrados')} neste coletor.`);
      return lines;
    }
    case 'reconnect':
      return result.reconnected === true
        ? [`Reconectado. Fila de envio: ${plural(n('queue_pending') ?? 0, 'item', 'itens')}.`]
        : [];
    case 'ping_host':
      return typeof result.ip === 'string'
        ? [`${result.ip}: ${result.reachable === true ? 'responde na rede' : 'não respondeu'}.`]
        : [];
    case 'set_server':
      return typeof result.to === 'string' ? [`Coletor passou a usar ${result.to}.`] : [];
    default:
      return [];
  }
}
