import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';

import { ApiError } from '../../lib/api';
import { AuthContext, type AuthContextValue } from '../../lib/auth-context';
import { LoginPage } from './AuthPages';

function renderLogin(login: AuthContextValue['login']) {
  const value: AuthContextValue = {
    status: 'anonymous',
    user: null,
    limited: null,
    login,
    logout: () => Promise.resolve(),
    applySession: vi.fn(),
    setUser: vi.fn(),
    can: () => false,
  };
  // O rodapé (ApiStatus) consulta /api/health: sem rede no teste, ele mostra "sem conexão".
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('sem rede no teste'))),
  );
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  render(
    <AuthContext.Provider value={value}>
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LoginPage', () => {
  it('pede o código do autenticador quando a API responde totp_required e reenvia com o código', async () => {
    const login = vi
      .fn<AuthContextValue['login']>()
      .mockRejectedValueOnce(new ApiError(401, 'totp_required', 'Informe o código do autenticador'))
      .mockResolvedValueOnce(undefined);
    renderLogin(login);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('E-mail'), 'admin@dati.local');
    await user.type(screen.getByLabelText('Senha'), 'segredo-forte');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));

    const code = await screen.findByLabelText(/Código do autenticador/);
    expect(login).toHaveBeenLastCalledWith('admin@dati.local', 'segredo-forte', undefined);
    await user.type(code, '12a3456');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(login).toHaveBeenLastCalledWith('admin@dati.local', 'segredo-forte', '123456');
  });

  it('mostra na tela e no console o motivo da recusa', async () => {
    const login = vi
      .fn<AuthContextValue['login']>()
      .mockRejectedValue(new ApiError(401, 'invalid_credentials', 'E-mail ou senha incorretos'));
    renderLogin(login);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText('E-mail'), 'x@y.z');
    await user.type(screen.getByLabelText('Senha'), 'errada');
    await user.click(screen.getByRole('button', { name: 'Entrar' }));
    expect(await screen.findByText('E-mail ou senha incorretos')).toBeInTheDocument();
    expect(console.error).toHaveBeenCalledWith('Login recusado:', expect.any(ApiError));
  });
});
