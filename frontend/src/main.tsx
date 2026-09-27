import './index.css';

import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { Toaster } from 'sonner';

import { AuthProvider } from './lib/AuthProvider';
import { ApiError } from './lib/api';
import { applyTheme, storedTheme } from './lib/theme';
import { product } from './product';
import { createRouter } from './router';

document.title = product.name;
applyTheme(storedTheme());

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Elemento #root não encontrado no index.html');
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('Erro não tratado:', event.reason);
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      // Erro de permissão/validação não melhora repetindo; falha de rede sim.
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
    },
  },
});

const router = createRouter();

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <TooltipPrimitive.Provider delayDuration={300}>
          <RouterProvider router={router} />
          <Toaster richColors closeButton position="top-right" />
        </TooltipPrimitive.Provider>
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
);
