import type { Schemas } from './api';

/** O que o dm-watchdog relata (e o que o coletor vê do serviço dele), vindo de `agent.watchdog_status`. */
export interface WatchdogStatus {
  agentState?: string;
  agentHealthy?: boolean;
  agentVersion?: string;
  agentMemoryBytes?: number;
  previousAgentVersion?: string;
  serviceState?: string;
  errors: string[];
  restarts: { at: string; reason: string }[];
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined;
}

/** Lê o JSON livre do servidor campo a campo (nada é assumido sem conferir o tipo). */
export function watchdogStatus(agent: Schemas['AgentOut']): WatchdogStatus {
  const raw = agent.watchdog_status;
  const restarts = Array.isArray(raw.restarts) ? (raw.restarts as unknown[]) : [];
  const errors = Array.isArray(raw.errors) ? (raw.errors as unknown[]) : [];
  return {
    agentState: str(raw.agent_state),
    agentHealthy: typeof raw.agent_healthy === 'boolean' ? raw.agent_healthy : undefined,
    agentVersion: str(raw.agent_version),
    agentMemoryBytes: typeof raw.agent_memory_bytes === 'number' ? raw.agent_memory_bytes : undefined,
    previousAgentVersion: str(raw.previous_agent_version),
    serviceState: str(raw.service_state),
    errors: errors.filter((e): e is string => typeof e === 'string'),
    restarts: restarts.flatMap((r) => {
      if (typeof r !== 'object' || r === null) return [];
      const { at, reason } = r as Record<string, unknown>;
      return typeof at === 'string' && typeof reason === 'string' ? [{ at, reason }] : [];
    }),
  };
}
