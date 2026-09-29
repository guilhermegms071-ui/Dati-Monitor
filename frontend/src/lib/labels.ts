import type { Tone } from '../components/ui/primitives';

export const DEVICE_STATUS: Record<string, { label: string; tone: Tone }> = {
  ready: { label: 'Pronta', tone: 'green' },
  printing: { label: 'Imprimindo', tone: 'blue' },
  warmup: { label: 'Aquecendo', tone: 'blue' },
  energy_saving: { label: 'Economia de energia', tone: 'purple' },
  warning: { label: 'Atenção', tone: 'yellow' },
  error: { label: 'Erro', tone: 'red' },
  offline: { label: 'Sem resposta', tone: 'red' },
  unknown: { label: 'Desconhecido', tone: 'gray' },
};

export const AGENT_STATE: Record<string, { label: string; tone: Tone }> = {
  online: { label: 'Online', tone: 'green' },
  degraded: { label: 'Degradado', tone: 'yellow' },
  paused: { label: 'Pausado', tone: 'purple' },
  offline: { label: 'Offline', tone: 'red' },
};

export const COMMAND_STATE: Record<string, { label: string; tone: Tone }> = {
  pending: { label: 'Aguardando', tone: 'gray' },
  sent: { label: 'Enviado', tone: 'blue' },
  acked: { label: 'Recebido', tone: 'blue' },
  running: { label: 'Executando', tone: 'yellow' },
  succeeded: { label: 'Concluído', tone: 'green' },
  failed: { label: 'Falhou', tone: 'red' },
  expired: { label: 'Expirou', tone: 'gray' },
  cancelled: { label: 'Cancelado', tone: 'gray' },
};

export const FINAL_COMMAND_STATES = new Set(['succeeded', 'failed', 'expired', 'cancelled']);

export const ROLE_LABEL: Record<string, string> = {
  superadmin: 'Superadministrador',
  reseller_admin: 'Administrador da revenda',
  operator: 'Operador',
  technician: 'Técnico',
  customer_viewer: 'Cliente (somente leitura)',
};

export const DEVICE_EVENT: Record<string, string> = {
  discovered: 'Descoberto',
  ip_changed: 'Troca de IP',
  moved_site: 'Mudou de local',
  replaced: 'Equipamento substituído no IP',
  counter_regression: 'Contador regrediu',
  reactivated: 'Reativado',
  deactivated: 'Desativado',
  manual_adjust: 'Ajuste manual de leitura',
};

export const SUPPLY_COLOR: Record<string, { short: string; bar: string }> = {
  cyan: { short: 'C', bar: 'bg-cyan-500' },
  magenta: { short: 'M', bar: 'bg-fuchsia-500' },
  yellow: { short: 'Y', bar: 'bg-yellow-400' },
  black: { short: 'K', bar: 'bg-slate-800 dark:bg-slate-300' },
};

export const CLUSTER_REASON: Record<string, string> = {
  first_agent: 'Primeiro coletor do local',
  manual_promote: 'Promovido pelo operador',
  lease_expired: 'Lease do MASTER expirou',
  preferred_master: 'MASTER preferido assumiu',
  revoked: 'Coletor revogado',
  deleted: 'Coletor excluído',
};
