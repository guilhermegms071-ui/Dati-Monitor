// Fluxo manual da Fase 9 (fora da suíte): Computadores/USB, leitura manual, tema escuro e celular.
// Uso: node e2e/manual-f9.mjs <pasta-de-saida>   (com o scripts\dev.ps1 no ar)
// Além das capturas, abre todas as telas do menu a 390 px no tema escuro e lista as que rolam na
// horizontal (a página inteira; tabelas largas devem rolar dentro do próprio cartão).
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

const ROUTES = [
  '/',
  '/parque',
  '/descobertas',
  '/alertas',
  '/trocas-de-toner',
  '/alertas-da-impressora',
  '/perfis',
  '/coletores',
  '/computadores',
  '/relatorios',
  '/clientes',
  '/mapa',
  '/empresas',
  '/usuarios',
  '/permissoes',
  '/campos-personalizados',
  '/integracao',
  '/auditoria',
  '/downloads',
  '/versoes',
];

const browser = await chromium.launch();

async function session(viewport, colorScheme) {
  const ctx = await browser.newContext({ viewport, colorScheme });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`${page.url()}: ${m.text()}`);
  });
  await page.goto(`${base}/login`);
  await page.getByLabel('E-mail').fill('e2e@dati.local');
  await page.getByLabel('Senha').fill(password);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL(`${base}/`);
  return { page, errors };
}

async function openComputers(page) {
  await page.goto(`${base}/computadores`);
  await page.getByLabel('Buscar computador').fill('Coletor E2E');
  await page.getByTestId('computers').getByRole('button').filter({ hasText: 'PC-E2E' }).click();
  await page.getByText('automático (PJL)').waitFor({ timeout: 20_000 });
  await page.waitForTimeout(800);
}

const light = await session({ width: 1440, height: 900 }, 'light');
await openComputers(light.page);
await light.page.screenshot({ path: `${out}/f9-01-computadores.png`, fullPage: true });
await light.page
  .getByRole('row')
  .filter({ hasText: 'USB-E2E000000001' })
  .getByRole('button', { name: 'Registrar leitura' })
  .click();
await light.page.getByRole('dialog').waitFor();
await light.page.waitForTimeout(500);
await light.page.screenshot({ path: `${out}/f9-02-leitura-manual.png` });

const dark = await session({ width: 1440, height: 900 }, 'dark');
await openComputers(dark.page);
await dark.page.screenshot({ path: `${out}/f9-03-computadores-escuro.png`, fullPage: true });

const phone = await session({ width: 390, height: 844 }, 'dark');
await openComputers(phone.page);
await phone.page.screenshot({ path: `${out}/f9-04-computadores-celular.png`, fullPage: true });
const overflow = [];
for (const route of ROUTES) {
  await phone.page.goto(`${base}${route}`);
  // Sem 'networkidle': o portal mantém o SSE ao vivo aberto.
  await phone.page.waitForTimeout(2000);
  const w = await phone.page.evaluate(() => document.documentElement.scrollWidth);
  if (w > 390) overflow.push(`${route} (${w}px)`);
  const name = route === '/' ? 'dashboard' : route.slice(1);
  await phone.page.screenshot({ path: `${out}/f9-celular-${name}.png` });
}
console.log('telas que rolam na horizontal a 390 px:', JSON.stringify(overflow));
console.log('erros no console:', JSON.stringify([...light.errors, ...dark.errors, ...phone.errors]));
await browser.close();
