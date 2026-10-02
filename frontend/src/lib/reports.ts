import type { Schemas } from './api';
import { fmtDate, fmtDateTime, fmtDec, fmtInt } from './format';

type Column = Schemas['ReportColumn'];

export const NUMERIC = new Set<Column['kind']>(['int', 'decimal', 'money', 'percent']);

function money(v: number): string {
  return v.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

/** Valor de uma célula de relatório formatado em pt-BR conforme o tipo da coluna. */
export function cellText(col: Pick<Column, 'kind'>, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  switch (col.kind) {
    case 'int':
      return fmtInt(Number(value));
    case 'decimal':
      return fmtDec(Number(value));
    case 'money':
      return money(Number(value));
    case 'percent':
      return `${fmtDec(Number(value))}%`;
    case 'bool':
      return value === true ? 'Sim' : 'Não';
    case 'datetime':
      return typeof value === 'string' ? fmtDateTime(value) : '—';
    case 'date':
      return typeof value === 'string' ? fmtDate(value) : '—';
    case 'text':
      return typeof value === 'string' ? value : JSON.stringify(value);
  }
}
