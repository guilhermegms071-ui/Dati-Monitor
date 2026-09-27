import { defineConfig, devices } from '@playwright/test';

import { python, repoRoot } from './e2e/paths';

/**
 * E2E contra o ambiente real: API (porta 8000) e portal Vite (porta 5173).
 * Se já estiverem rodando (scripts\dev.ps1), são reutilizados; senão o Playwright os inicia.
 * O global-setup prepara usuário, cliente, coletor e leituras pelos fluxos reais (scripts/e2e_seed.py).
 */

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  timeout: 30_000,
  // Os testes dividem o mesmo usuário/banco e o do agente real compila o dm-agent: em série é determinístico.
  workers: 1,
  expect: { timeout: 10_000 },
  retries: 0,
  reporter: [['list']],
  use: { baseURL: 'http://localhost:5173', trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.api.main:create_app --factory --host 127.0.0.1 --port 8000`,
      cwd: `${repoRoot}backend`,
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      // Gateway WebSocket: comandos ao vivo para o dm-agent real (e2e/agent.spec.ts).
      command: `"${python}" -m uvicorn app.gateway.main:create_app --factory --host 127.0.0.1 --port 8001 --ws websockets-sansio`,
      cwd: `${repoRoot}backend`,
      url: 'http://127.0.0.1:8001/health',
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      // As 8 impressoras simuladas em portas próprias do E2E (12161-12168); 12160 responde quando estão prontas.
      command: `"${python}" scripts/e2e_sims.py --base 12160 --ready-port 12160`,
      cwd: repoRoot,
      url: 'http://127.0.0.1:12160/',
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
