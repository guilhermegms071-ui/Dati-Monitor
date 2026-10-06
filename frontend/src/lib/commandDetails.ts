/** Resultado de comando → linhas legíveis em português (sem JSON na tela). */

const LABELS: Record<string, string> = {
  ok: 'Resultado',
  error: 'Erro',
  ip: 'IP',
  port: 'Porta',
  serial: 'Nº de série',
  mac: 'MAC',
  hostname: 'Nome do PC',
  host: 'Endereço',
  version: 'Versão',
  os: 'Sistema',
  arch: 'Arquitetura',
  state: 'Situação',
  connected: 'Conectado',
  reachable: 'Responde na rede',
  reconnected: 'Reconectado',
  paused: 'Coletas pausadas',
  queue_pending: 'Fila de envio',
  pending: 'Pendentes',
  dead: 'Descartados',
  latency_ms: 'Latência',
  rtt_ms: 'Tempo de resposta',
  rtts_ms: 'Tempos de resposta',
  elapsed_ms: 'Duração',
  duration_s: 'Duração',
  uptime_s: 'Ligado há',
  down_for_s: 'Fora do ar há',
  offset_s: 'Diferença de relógio',
  server_utc: 'Hora do servidor',
  local_utc: 'Hora do PC',
  server_url: 'Servidor',
  ws_url: 'Canal ao vivo',
  url: 'Endereço',
  cluster_role: 'Papel',
  applied_config_version: 'Versão da configuração',
  free_bytes: 'Espaço livre',
  total_bytes: 'Espaço total',
  bytes: 'Tamanho',
  files: 'Arquivos',
  hours: 'Horas',
  path: 'Caminho',
  status: 'Situação',
  value: 'Valor',
  sys_descr: 'Descrição',
  sys_object_id: 'Identificador do modelo',
  is_printer: 'É impressora',
  name: 'Nome',
  open: 'Aberta',
  up: 'Ativa',
  sent: 'Enviados',
  received: 'Recebidos',
  answered: 'Responderam',
  refused: 'Recusados',
  requested: 'Pedidos',
  failed: 'Falhas',
  printers_found: 'Impressoras encontradas',
  probed: 'Endereços testados',
  targets: 'Endereços na faixa',
  ranges: 'Faixas',
  new: 'Novas',
  removed: 'Removidas',
  from: 'De',
  to: 'Para',
  clock: 'Relógio',
  https: 'Conexão com o servidor',
  websocket: 'Canal ao vivo',
  disk: 'Disco',
  dns: 'DNS',
  icmp: 'Ping',
  tcp: 'Portas',
  interfaces: 'Placas de rede',
  note: 'Observação',
  last_error: 'Último erro',
  version_agent: 'Versão do coletor',
};

export interface DetailRow {
  label: string;
  value: string;
}

export interface DeviceRow {
  serial: string;
  ip: string;
  ok: boolean;
  error: string | null;
}

function label(key: string): string {
  return LABELS[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

function value(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') {
    if (key === 'ok') return v ? 'OK' : 'Falhou';
    return v ? 'Sim' : 'Não';
  }
  if (typeof v === 'number') {
    const n = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(v);
    if (key.endsWith('_ms')) return `${n} ms`;
    if (key.endsWith('_s')) return `${n} s`;
    if (key.endsWith('_bytes') || key === 'bytes') {
      const units = ['B', 'KB', 'MB', 'GB', 'TB'];
      let x = v;
      let u = 0;
      while (x >= 1024 && u < units.length - 1) {
        x /= 1024;
        u += 1;
      }
      return `${new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(x)} ${units[u] ?? 'B'}`;
    }
    return n;
  }
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) {
    return v.every((x) => typeof x !== 'object' || x === null)
      ? v.map((x) => value(key, x)).join(', ')
      : `${String(v.length)} item(ns)`;
  }
  return '—';
}

/** Linhas "rótulo: valor"; objetos aninhados viram "Grupo · campo". `skip` = chaves mostradas de outro jeito. */
export function detailRows(result: Record<string, unknown>, skip: string[] = []): DetailRow[] {
  const rows: DetailRow[] = [];
  const walk = (obj: Record<string, unknown>, prefix: string, depth: number) => {
    for (const [k, v] of Object.entries(obj)) {
      if (!prefix && skip.includes(k)) continue;
      const name = prefix ? `${prefix} · ${label(k)}` : label(k);
      if (v && typeof v === 'object' && !Array.isArray(v) && depth < 2) {
        walk(v as Record<string, unknown>, name, depth + 1);
      } else {
        rows.push({ label: name, value: value(k, v) });
      }
    }
  };
  walk(result, '', 0);
  return rows;
}

/** Lista de equipamentos do resultado (Ler agora, varredura): serial, IP e se deu certo. */
export function deviceRows(result: Record<string, unknown>): DeviceRow[] {
  const list = result.devices;
  if (!Array.isArray(list)) return [];
  return list.flatMap((d) => {
    if (!d || typeof d !== 'object') return [];
    const o = d as Record<string, unknown>;
    return [
      {
        serial: typeof o.serial === 'string' && o.serial ? o.serial : '—',
        ip: typeof o.ip === 'string' ? o.ip : '—',
        ok: o.ok === true,
        error: typeof o.error === 'string' ? o.error : null,
      },
    ];
  });
}
