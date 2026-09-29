import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';

import { python, repoRoot } from './paths';

/**
 * Prepara o banco de desenvolvimento pelos fluxos reais (scripts/e2e_seed.py): usuário e2e@dati.local
 * com senha aleatória desta execução, cliente/local, coletor instalado e leituras de uma impressora.
 * A senha só existe em memória (variável de ambiente herdada pelos workers do Playwright).
 */
export default function globalSetup(): void {
  const password = randomBytes(18).toString('base64url');
  const out = execFileSync(python, [`${repoRoot}scripts/e2e_seed.py`], {
    env: { ...process.env, DM_E2E_PASSWORD: password, PYTHONIOENCODING: 'utf-8' },
    encoding: 'utf-8',
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  const seed = JSON.parse(out.trim().split('\n').pop() ?? '{}') as {
    email: string;
    device_serial: string;
    total: number;
    customer: string;
    real_site_id: string;
    real_site_name: string;
  };
  process.env.DM_E2E_EMAIL = seed.email;
  process.env.DM_E2E_PASSWORD = password;
  process.env.DM_E2E_SERIAL = seed.device_serial;
  process.env.DM_E2E_TOTAL = String(seed.total);
  process.env.DM_E2E_CUSTOMER = seed.customer;
  process.env.DM_E2E_REAL_SITE = seed.real_site_id;
  process.env.DM_E2E_REAL_SITE_NAME = seed.real_site_name;
}
