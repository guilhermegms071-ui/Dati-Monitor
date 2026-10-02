// Fluxo manual da Fase 8 (fora da suíte): Downloads e links de instalação no banco de desenvolvimento.
// Uso: node e2e/manual-f7.mjs <pasta-de-saida>   (com o scripts\dev.ps1 no ar)
// A senha do usuário de teste é aleatória e fica só em memória (o seed a recebe por variável de ambiente).
import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';

import { chromium } from '@playwright/test';

const out = process.argv[2];
const base = 'http://localhost:5173';
const repo = new URL('../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const python = `${repo}.venv/Scripts/python.exe`;
const password = `E2e-${randomBytes(12).toString('base64url')}`;
execFileSync(python, [`${repo}scripts/e2e_seed.py`], {
  env: { ...process.env, DM_E2E_PASSWORD: password, PYTHONIOENCODING: 'utf-8' },
  stdio: ['ignore', 'ignore', 'inherit'],
});

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text());
});
await page.goto(`${base}/login`);
await page.getByLabel('E-mail').fill('e2e@dati.local');
await page.getByLabel('Senha').fill(password);
await page.getByRole('button', { name: 'Entrar' }).click();
await page.waitForURL(`${base}/`);

async function shot(name, path, wait) {
  await page.goto(`${base}${path}`);
  if (wait) await page.getByText(wait).first().waitFor({ timeout: 20_000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
}

await shot('f8-01-downloads', '/downloads', 'Instaladores do coletor');
await page.goto(`${base}/coletores`);
await page.getByRole('button', { name: 'Novo coletor' }).click();
const dialog = page.getByRole('dialog');
await dialog.getByLabel('Cliente').fill('Cliente E2E');
await page.getByRole('option', { name: 'Cliente E2E', exact: true }).click();
await dialog.getByLabel('Local').fill('Local E2E');
await page.getByRole('option', { name: 'Local E2E', exact: true }).click();
await dialog.getByLabel('Nome do coletor').fill('Coletor captura F8');
await dialog.getByRole('button', { name: 'Gerar código' }).click();
await dialog.getByTestId('installer-link').waitFor();
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/f8-02-novo-coletor.png` });
console.log('erros no console:', JSON.stringify(errors));
await browser.close();
