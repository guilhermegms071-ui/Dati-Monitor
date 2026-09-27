import { toast } from 'sonner';

import { errorMessage } from './utils';

/** Toda falha aparece na tela (toast) e no console (regra "erros nunca silenciosos"). */
export function showError(err: unknown, context?: string): void {
  const msg = errorMessage(err);
  console.error(context ? `${context}:` : 'Erro:', err);
  toast.error(context ? `${context}: ${msg}` : msg);
}

export function showSuccess(message: string): void {
  toast.success(message);
}
