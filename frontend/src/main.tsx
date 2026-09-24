import './index.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import { product } from './product';

document.title = product.name;

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Elemento #root não encontrado no index.html');
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('Erro não tratado:', event.reason);
});

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
