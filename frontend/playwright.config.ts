import { defineConfig, devices } from '@playwright/test';

/**
 * E2E contra o ambiente real: API (porta 8000) e portal Vite (porta 5173).
 * Se já estiverem rodando (scripts\dev.ps1), são reutilizados; senão o Playwright os inicia.
 */
const repoRoot = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const python = process.platform === 'win32' ? `${repoRoot}.venv/Scripts/python.exe` : `${repoRoot}.venv/bin/python`;

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
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
      command: 'npm run dev',
      url: 'http://localhost:5173',
      reuseExistingServer: true,
      timeout: 60_000,
    },
  ],
});
