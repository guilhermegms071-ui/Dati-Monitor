import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Schemas } from '../../lib/api';
import { serverAddressError } from '../../lib/serverAddress';
import { SetServerDialog } from './SetServerDialog';

const agent = { id: 'a1', name: 'PC da recepção' } as Schemas['AgentOut'];

describe('SetServerDialog', () => {
  it('aplica a mesma regra de endereço do servidor', () => {
    expect(serverAddressError('https://monitor.exemplo.com.br')).toBeNull();
    expect(serverAddressError('http://10.10.10.25:8000')).toBeNull();
    expect(serverAddressError('http://192.168.0.9:8000')).toBeNull();
    expect(serverAddressError('http://monitor.exemplo.com.br')).toMatch(/IP de rede privada/);
    expect(serverAddressError('http://8.8.8.8:8000')).toMatch(/IP de rede privada/);
    expect(serverAddressError('https://monitor.exemplo.com.br/api')).toMatch(/sem caminho/);
    expect(serverAddressError('ftp://10.0.0.1')).toMatch(/https/);
    expect(serverAddressError('', true)).toBeNull();
    expect(serverAddressError('ws://monitor.exemplo.com.br/ws/agent', true)).toMatch(/IP de rede privada/);
  });

  it('envia o comando set_server só com endereço válido', async () => {
    const send = vi.fn().mockResolvedValue(null);
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<SetServerDialog agent={agent} send={send} onClose={onClose} />);

    const submit = screen.getByRole('button', { name: 'Enviar ao coletor' });
    expect(submit).toBeDisabled();
    await user.type(screen.getByLabelText('Novo endereço do servidor'), 'http://monitor.exemplo.com.br');
    expect(submit).toBeDisabled();
    await user.clear(screen.getByLabelText('Novo endereço do servidor'));
    await user.type(screen.getByLabelText('Novo endereço do servidor'), 'https://monitor.exemplo.com.br');
    await user.click(submit);
    expect(send).toHaveBeenCalledWith('set_server', { server_url: 'https://monitor.exemplo.com.br' });
    expect(onClose).toHaveBeenCalled();
  });
});
