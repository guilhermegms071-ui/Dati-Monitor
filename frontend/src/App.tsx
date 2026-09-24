import { ApiStatus } from './components/ApiStatus';
import { product } from './product';

export function App() {
  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold text-slate-900">{product.name}</h1>
      <p className="mb-4 text-slate-600">Portal de monitoramento de impressoras — ambiente de desenvolvimento.</p>
      <ApiStatus />
    </main>
  );
}
