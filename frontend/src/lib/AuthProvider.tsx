import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';

import { api, csrfToken, refreshSession, session, unwrap, type Me, type TokenResponse } from './api';
import { AuthContext, type AuthContextValue, type AuthState } from './auth-context';

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  // Sem o cookie de CSRF (emitido junto com o de refresh) não há sessão a recuperar: nem tenta o refresh.
  const [state, setState] = useState<AuthState>(() => ({
    status: csrfToken() ? 'loading' : 'anonymous',
    user: null,
    limited: null,
  }));

  const applySession = useCallback((resp: TokenResponse) => {
    session.set(resp.access_token);
    setState({ status: 'authenticated', user: resp.user, limited: resp.limited ?? null });
  }, []);

  const clear = useCallback(() => {
    session.set(null);
    queryClient.clear();
    setState({ status: 'anonymous', user: null, limited: null });
  }, [queryClient]);

  useEffect(() => {
    // Recarregou a página: tenta recuperar a sessão pelo cookie de refresh.
    let active = true;
    session.onUnauthorized(clear);
    if (!csrfToken()) {
      return () => {
        session.onUnauthorized(null);
      };
    }
    void refreshSession().then((resp) => {
      if (!active) return;
      if (resp) applySession(resp);
      else setState({ status: 'anonymous', user: null, limited: null });
    });
    return () => {
      active = false;
      session.onUnauthorized(null);
    };
  }, [applySession, clear]);

  const login = useCallback(
    async (email: string, password: string, totpCode?: string) => {
      const resp = await unwrap(
        api.POST('/api/v1/auth/login', { body: { email, password, totp_code: totpCode || null } }),
      );
      applySession(resp);
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    try {
      await fetch('/api/v1/auth/logout', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': csrfToken() },
      });
    } catch (err) {
      console.error('Falha ao encerrar a sessão no servidor:', err);
    } finally {
      clear();
    }
  }, [clear]);

  const setUser = useCallback((user: Me) => {
    setState((s) => ({ ...s, user }));
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      logout,
      applySession,
      setUser,
      can: (permission: string) => state.user?.permissions.includes(permission) ?? false,
    }),
    [state, login, logout, applySession, setUser],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
