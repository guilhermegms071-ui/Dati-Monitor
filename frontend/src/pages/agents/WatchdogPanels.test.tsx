import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Schemas } from '../../lib/api';
import { watchdogStatus } from '../../lib/watchdog';
import { UninstallDialog, WatchdogBadge } from './WatchdogPanels';

function agent(over: Partial<Schemas['AgentOut']> = {}): Schemas['AgentOut'] {
  return {
    id: 'a1',
    reseller_id: 'r1',
    site_id: 's1',
    name: 'PC da recepção',
    kind: 'windows',
    hostname: 'RECEPCAO-01',
    os: 'Windows 11',
    arch: 'amd64',
    local_ips: [],
    host_mac: null,
    public_ip: null,
    install_path: null,
    monitor_local_networks: false,
    version: '1.0.0',
    watchdog_version: '1.0.2',
    update_channel: 'stable',
    cluster_role: 'master',
    priority: 100,
    state: 'online',
    last_seen_at: '2026-09-27T12:00:00Z',
    last_watchdog_seen_at: '2026-09-27T12:00:00Z',
    enrolled_at: '2026-09-20T12:00:00Z',
    revoked_at: null,
    config_version: 3,
    applied_config_version: 3,
    queue_pending: 0,
    uptime_seconds: 100,
    cpu_percent: 1,
    memory_bytes: 1,
    avg_latency_ms: 2,
    last_error: null,
    uninstalled_at: null,
    suggested_ranges: [],
    paused: false,
    ws_connected: true,
    watchdog_status: {},
    watchdog_alive: true,
    customer_id: 'c1',
    customer_name: 'Cliente',
    site_name: 'Matriz',
    created_at: '2026-09-20T12:00:00Z',
    updated_at: '2026-09-27T12:00:00Z',
    ...over,
  };
}

describe('WatchdogBadge', () => {
  it('mostra ativo, sem sinal ou não instalado', () => {
    const { rerender } = render(<WatchdogBadge agent={agent()} />);
    expect(screen.getByText('ativo · 1.0.2')).toBeInTheDocument();
    rerender(<WatchdogBadge agent={agent({ watchdog_alive: false })} />);
    expect(screen.getByText(/sem sinal desde 27\/09\/2026, 09:00/)).toBeInTheDocument();
    rerender(<WatchdogBadge agent={agent({ watchdog_alive: false, last_watchdog_seen_at: null })} />);
    expect(screen.getByText('não instalado')).toBeInTheDocument();
  });
});

describe('watchdogStatus', () => {
  it('lê só o que tem o tipo certo', () => {
    const s = watchdogStatus(
      agent({
        watchdog_status: {
          agent_state: 'running',
          agent_healthy: 'sim',
          previous_agent_version: '0.9.0',
          restarts: [{ at: '2026-09-27T11:00:00Z', reason: 'memória acima de 300 MB' }, { at: 1 }, 'x'],
          errors: ['saúde do coletor: sem resposta', 3],
        },
      }),
    );
    expect(s).toEqual({
      agentState: 'running',
      agentHealthy: undefined,
      agentVersion: undefined,
      agentMemoryBytes: undefined,
      previousAgentVersion: '0.9.0',
      serviceState: undefined,
      errors: ['saúde do coletor: sem resposta'],
      restarts: [{ at: '2026-09-27T11:00:00Z', reason: 'memória acima de 300 MB' }],
    });
  });
});

describe('UninstallDialog', () => {
  it('exige a confirmação dupla: aviso e o nome digitado exatamente', async () => {
    const send = vi.fn(() => Promise.resolve(null));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<UninstallDialog agent={agent()} send={send} onClose={onClose} />);

    expect(screen.getByText(/remove os serviços do coletor/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Entendi, continuar' }));
    const confirm = screen.getByRole('button', { name: 'Desinstalar agora' });
    expect(confirm).toBeDisabled();
    await user.type(screen.getByLabelText(/Digite o nome do coletor/), 'PC da recepcao');
    expect(confirm).toBeDisabled();
    await user.clear(screen.getByLabelText(/Digite o nome do coletor/));
    await user.type(screen.getByLabelText(/Digite o nome do coletor/), 'PC da recepção');
    await user.click(confirm);
    expect(send).toHaveBeenCalledWith('uninstall', { confirm_name: 'PC da recepção' });
    expect(onClose).toHaveBeenCalled();
  });
});
