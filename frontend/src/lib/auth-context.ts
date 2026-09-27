import { createContext, useContext } from 'react';

import type { Me, TokenResponse } from './api';

export type Limited = TokenResponse['limited'];

export interface AuthState {
  status: 'loading' | 'anonymous' | 'authenticated';
  user: Me | null;
  /** Sessão limitada: precisa trocar a senha ou configurar o TOTP antes de usar o portal. */
  limited: Limited | null;
}

export interface AuthContextValue extends AuthState {
  login: (email: string, password: string, totpCode?: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Aplica uma nova sessão devolvida pela API (troca de senha, ativação de TOTP). */
  applySession: (resp: TokenResponse) => void;
  setUser: (user: Me) => void;
  can: (permission: string) => boolean;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth fora do AuthProvider');
  return ctx;
}
