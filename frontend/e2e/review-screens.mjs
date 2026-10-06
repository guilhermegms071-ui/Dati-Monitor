// Revisão visual (fora da suíte): captura as telas principais do portal.
// Uso: DM_E2E_EMAIL=... DM_E2E_PASSWORD=... node e2e/review-screens.mjs <pasta> [claro|escuro]
import { chromium } from '@playwright/test';

const out = process.argv[2];
const scheme = process.argv[3] === 'escuro' ? 'dark' : 'light';
const base = 'http://localhost:5173';
const browser = await chromium.launch(process.env.DM_CHROMIUM ? { executablePath: process.env.DM_CHROMIUM } : {});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: scheme });
const page = await ctx.newPage();
const errors = [];
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text());
});
page.on('response', (r) => {
  if (r.status() >= 400) errors.push(`${String(r.status())} ${r.url()}`);
});
await page.goto(`${base}/login`);
await page.getByLabel('E-mail').waitFor();
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/00-login.png` });
await page.getByLabel('E-mail').fill(process.env.DM_E2E_EMAIL);
await page.getByLabel('Senha').fill(process.env.DM_E2E_PASSWORD);
await page.getByRole('button', { name: 'Entrar' }).click();
await page.waitForURL(`${base}/`);

async function shot(name, path, wait) {
  await page.goto(`${base}${path}`);
  if (wait) await page.getByText(wait).first().waitFor({ timeout: 15_000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/${name}.png` });
}

await shot('01-visao-geral', '/', null);
await shot('02-parque', '/parque', 'E2E-0001');
await page.getByRole('link', { name: 'E2E-0001' }).first().click();
await page.waitForTimeout(2500);
await page.screenshot({ path: `${out}/03-impressora.png`, fullPage: true });
await shot('04-coletores', '/coletores', null);
await shot('05-clientes', '/clientes', 'Cliente E2E');
await page.getByRole('button', { name: /Novo cliente/ }).click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/06-novo-cliente.png` });
await shot('07-descobertas', '/descobertas', null);
await shot('08-alertas', '/alertas', null);
console.log('erros no console:', JSON.stringify(errors));
await browser.close();
