import { Link } from 'react-router';

import { EmptyState } from '../components/ui/primitives';

export function NotFoundPage() {
  return (
    <EmptyState title="Página não encontrada">
      <p>O endereço não existe ou você não tem acesso a ele.</p>
      <Link to="/" className="text-brand-600 hover:underline">
        Voltar ao início
      </Link>
    </EmptyState>
  );
}
