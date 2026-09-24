import { render, screen } from '@testing-library/react';

import type { HealthResponse } from '../api/health';
import { ApiStatus } from './ApiStatus';

const okHealth: HealthResponse = {
  status: 'ok',
  service: 'api',
  product: 'Dati Monitor',
  version: '0.1.0',
  database: { ok: true, latency_ms: 1.2, server_version: '16.15', error: null },
};

describe('ApiStatus', () => {
  it('mostra "conectada" quando a API e o banco respondem', async () => {
    render(<ApiStatus load={() => Promise.resolve(okHealth)} />);
    expect(screen.getByRole('status')).toHaveTextContent('Verificando a API');
    expect(await screen.findByText(/API: conectada \(versão 0\.1\.0, PostgreSQL 16\.15\)/)).toBeInTheDocument();
  });

  it('mostra alerta e registra no console quando o banco está fora', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const degraded: HealthResponse = {
      ...okHealth,
      status: 'degraded',
      database: { ok: false, latency_ms: null, server_version: null, error: 'ConnectionRefusedError' },
    };
    render(<ApiStatus load={() => Promise.resolve(degraded)} />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'banco de dados está indisponível — ConnectionRefusedError',
    );
    expect(consoleError).toHaveBeenCalled();
  });

  it('mostra erro na tela e no console quando a API não responde', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<ApiStatus load={() => Promise.reject(new Error('Failed to fetch'))} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('API: sem conexão — Failed to fetch');
    expect(consoleError).toHaveBeenCalledWith('Falha ao consultar a API:', expect.any(Error));
  });
});
