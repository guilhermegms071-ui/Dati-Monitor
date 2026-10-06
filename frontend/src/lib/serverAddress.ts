const PRIVATE_IP = /^(10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)$/;

/** Mesma regra do servidor (schemas/commands.py): https:// sempre; http:// só com IP de rede privada. */
export function serverAddressError(value: string, ws = false): string | null {
  const v = value.trim();
  if (!v) return ws ? null : 'Informe o endereço.';
  let u: URL;
  try {
    u = new URL(v);
  } catch {
    return 'Endereço inválido.';
  }
  const [secure, plain] = ws ? ['wss:', 'ws:'] : ['https:', 'http:'];
  if (u.protocol !== secure && u.protocol !== plain) return `Use ${secure}//…`;
  if (u.protocol === plain && !PRIVATE_IP.test(u.hostname)) {
    return `${plain}// só é aceito com IP de rede privada; use ${secure}//…`;
  }
  if (!ws && u.pathname !== '/' && u.pathname !== '') return 'Informe só o endereço do servidor, sem caminho.';
  return null;
}
