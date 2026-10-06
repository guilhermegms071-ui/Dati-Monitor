/** Documentos e telefones brasileiros: máscara enquanto digita e conferência (mesma regra do servidor). */

const digits = (v: string) => v.replace(/\D/g, '');

/** 12.345.678/0001-90 enquanto digita (até 14 dígitos). */
export function maskCnpj(value: string): string {
  const d = digits(value).slice(0, 14);
  return d
    .replace(/^(\d{2})(\d)/, '$1.$2')
    .replace(/^(\d{2})\.(\d{3})(\d)/, '$1.$2.$3')
    .replace(/\.(\d{3})(\d)/, '.$1/$2')
    .replace(/(\d{4})(\d)/, '$1-$2');
}

/** CNPJ com 14 dígitos e dígitos verificadores corretos (app/core/validators.py). */
export function isValidCnpj(value: string): boolean {
  const d = digits(value);
  if (d.length !== 14 || /^(\d)\1{13}$/.test(d)) return false;
  for (const size of [12, 13]) {
    const weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2].slice(13 - size);
    let total = 0;
    for (let i = 0; i < size; i++) total += Number(d[i]) * (weights[i] ?? 0);
    const check = total % 11 < 2 ? 0 : 11 - (total % 11);
    if (Number(d[size]) !== check) return false;
  }
  return true;
}

/** (21) 3333-0001 ou (21) 99999-0001 enquanto digita. */
export function maskPhone(value: string): string {
  const d = digits(value).slice(0, 11);
  if (d.length <= 2) return d.length ? `(${d}` : '';
  if (d.length <= 6) return `(${d.slice(0, 2)}) ${d.slice(2)}`;
  if (d.length <= 10) return `(${d.slice(0, 2)}) ${d.slice(2, 6)}-${d.slice(6)}`;
  return `(${d.slice(0, 2)}) ${d.slice(2, 7)}-${d.slice(7)}`;
}

export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}
