/** Erros de validação da API ({"loc": [...], "msg": ...}) em português, por campo. */

interface ValidationItem {
  loc: unknown[];
  msg: string;
}

const FIELD_LABELS: Record<string, string> = {
  company_id: 'Empresa',
  customer_id: 'Cliente',
  site_id: 'Local',
  name: 'Nome',
  legal_name: 'Razão social',
  cnpj: 'CNPJ',
  email: 'E-mail',
  phone: 'Telefone',
  contact_name: 'Contato',
  erp_code: 'Código no ERP',
  password: 'Senha',
  ip: 'IP',
  serial: 'Número de série',
  zip_code: 'CEP',
  state: 'UF',
  city: 'Cidade',
};

function items(err: unknown): ValidationItem[] {
  if (!(err instanceof Error) || !('details' in err)) return [];
  const details = (err as { details: unknown }).details;
  if (typeof details !== 'object' || details === null || !('errors' in details)) return [];
  const list = details.errors;
  if (!Array.isArray(list)) return [];
  return list.filter(
    (e): e is ValidationItem =>
      typeof e === 'object' &&
      e !== null &&
      Array.isArray((e as ValidationItem).loc) &&
      typeof (e as ValidationItem).msg === 'string',
  );
}

/** Mensagem do Pydantic em português ("Value error, CNPJ inválido" → "CNPJ inválido"). */
export function translateMessage(msg: string): string {
  const value = /^Value error, (.+)$/.exec(msg);
  if (value?.[1]) return value[1];
  if (msg === 'Field required') return 'obrigatório';
  const min = /^String should have at least (\d+) characters?$/.exec(msg);
  if (min?.[1]) return `mínimo de ${min[1]} caracteres`;
  const max = /^String should have at most (\d+) characters?$/.exec(msg);
  if (max?.[1]) return `máximo de ${max[1]} caracteres`;
  if (msg.startsWith('Input should be a valid UUID')) return 'escolha uma opção da lista';
  if (msg.startsWith('Input should be a valid integer')) return 'número inteiro inválido';
  if (msg.startsWith('Input should be a valid email') || msg.includes('email address')) return 'e-mail inválido';
  return msg;
}

/** Campo → mensagem, para mostrar o erro embaixo de cada campo do formulário. */
export function fieldErrors(err: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  for (const e of items(err)) {
    const last = e.loc[e.loc.length - 1];
    const field = typeof last === 'string' || typeof last === 'number' ? String(last) : '';
    if (field && !out[field]) out[field] = translateMessage(e.msg);
  }
  return out;
}

/** Resumo legível: "Dados inválidos: CNPJ: CNPJ inválido; Empresa: obrigatório". */
export function validationSummary(err: unknown): string | null {
  const list = Object.entries(fieldErrors(err));
  if (!list.length) return null;
  return list
    .map(([field, msg]) => {
      const label = FIELD_LABELS[field] ?? field;
      return msg.toLowerCase().startsWith(label.toLowerCase()) ? msg : `${label}: ${msg}`;
    })
    .join('; ');
}
