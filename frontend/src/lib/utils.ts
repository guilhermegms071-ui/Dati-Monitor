import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

import { validationSummary } from './validation';

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** Mensagem legível de qualquer erro (para toasts e telas de erro). */
export function errorMessage(err: unknown): string {
  if (err instanceof Error) {
    // Erro de validação: diz qual campo está errado, não só "Dados inválidos".
    const fields = validationSummary(err);
    return fields ? `${err.message}: ${fields}` : err.message;
  }
  if (typeof err === 'string') return err;
  return 'Erro inesperado';
}
