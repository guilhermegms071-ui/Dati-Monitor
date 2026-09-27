import { Navigate, Outlet, useLocation } from 'react-router';

import { useAuth } from '../../lib/auth-context';
import { Spinner } from '../ui/primitives';

/** Anônimo vai para o login; sessão limitada (troca de senha / TOTP obrigatório) vai para /sessao. */
export function RequireAuth() {
  const { status, limited } = useAuth();
  const location = useLocation();
  if (status === 'loading') {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (limited) return <Navigate to="/sessao" replace />;
  return <Outlet />;
}
