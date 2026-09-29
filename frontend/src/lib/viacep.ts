interface SiteAddressFields {
  street: string | null;
  number: string | null;
  district: string | null;
  city: string | null;
  state: string | null;
}

/** Consulta de CEP no ViaCEP (seção 16.9): preenche logradouro, bairro, cidade e UF do Local. */

export interface CepAddress {
  cep: string;
  street: string;
  district: string;
  city: string;
  state: string;
}

export function normalizeCep(value: string): string | null {
  const digits = value.replace(/\D/g, '');
  return digits.length === 8 ? digits : null;
}

export function formatCep(value: string | null | undefined): string {
  const digits = (value ?? '').replace(/\D/g, '');
  return digits.length === 8 ? `${digits.slice(0, 5)}-${digits.slice(5)}` : digits;
}

interface ViaCepResponse {
  cep?: string;
  logradouro?: string;
  bairro?: string;
  localidade?: string;
  uf?: string;
  erro?: boolean | string;
}

/** Busca o CEP; lança erro com mensagem em português (CEP inválido, não encontrado ou serviço fora do ar). */
export async function lookupCep(value: string, fetcher: typeof fetch = fetch): Promise<CepAddress> {
  const cep = normalizeCep(value);
  if (!cep) throw new Error('CEP deve ter 8 dígitos');
  let resp: Response;
  try {
    resp = await fetcher(`https://viacep.com.br/ws/${cep}/json/`);
  } catch (err) {
    throw new Error(`Não foi possível consultar o ViaCEP: ${err instanceof Error ? err.message : String(err)}`, {
      cause: err,
    });
  }
  if (!resp.ok) throw new Error(`ViaCEP respondeu ${String(resp.status)}`);
  const data = (await resp.json()) as ViaCepResponse;
  if (data.erro) throw new Error(`CEP ${formatCep(cep)} não encontrado`);
  return {
    cep,
    street: data.logradouro ?? '',
    district: data.bairro ?? '',
    city: data.localidade ?? '',
    state: data.uf ?? '',
  };
}

/** Uma linha do endereço para listas: "Rua X, 10 · Centro · Rio de Janeiro/RJ". */
export function siteAddress(s: SiteAddressFields): string {
  const street = [s.street, s.number].filter(Boolean).join(', ');
  const city = [s.city, s.state].filter(Boolean).join('/');
  return [street, s.district, city].filter(Boolean).join(' · ');
}
