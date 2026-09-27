// Fluxo manual da Fase 4 (fora da suíte): captura as telas do portal para revisão visual.
// Uso: DM_E2E_EMAIL=... DM_E2E_PASSWORD=... node e2e/manual-screens.mjs <pasta-de-saida>
import { chromium } from '@playwright/test';

const out = process.argv[2];
const base = 'http://localhost:5173';
const browser = await chromium.launch();

async function session(viewport, colorScheme) {
  const ctx = await browser.newContext({ viewport, colorScheme });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });
  await page.goto(`${base}/login`);
  await page.getByLabel('E-mail').fill(process.env.DM_E2E_EMAIL);
  await page.getByLabel('Senha').fill(process.env.DM_E2E_PASSWORD);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await page.waitForURL(`${base}/`);
  return { ctx, page, errors };
}

async function shot(page, name, path, wait) {
  await page.goto(`${base}${path}`);
  if (wait) await page.getByText(wait).first().waitFor({ timeout: 15_000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: false });
}

const desk = await session({ width: 1440, height: 900 }, 'light');
await shot(desk.page, '01-dashboard', '/', 'Páginas por dia');
await shot(desk.page, '02-parque', '/parque', 'A797019500624');
const konica = desk.page.getByRole('link', { name: 'A797019500624' }).first();
await konica.click();
await desk.page.getByText('217.031').first().waitFor();
await desk.page.waitForTimeout(1200);
await desk.page.screenshot({ path: `${out}/03-equipamento.png` });
await shot(desk.page, '04-coletores', '/coletores', process.env.DM_AGENT_NAME);
await desk.page.getByRole('link', { name: process.env.DM_AGENT_NAME }).first().click();
await desk.page.waitForTimeout(2000);
await desk.page.screenshot({ path: `${out}/05-coletor.png` });
await desk.page.getByRole('tab', { name: 'Comandos' }).click();
await desk.page.waitForTimeout(1200);
await desk.page.screenshot({ path: `${out}/06-coletor-comandos.png` });
await shot(desk.page, '07-clientes', '/clientes', 'Cliente E2E');
await shot(desk.page, '08-usuarios', '/usuarios', 'e2e@dati.local');
await shot(desk.page, '09-auditoria', '/auditoria', 'Quando');
await shot(desk.page, '10-conta', '/conta', 'Aparência');
console.log('erros no console (claro):', JSON.stringify(desk.errors));

const dark = await session({ width: 1440, height: 900 }, 'dark');
await shot(dark.page, '11-parque-escuro', '/parque', 'A797019500624');
await shot(dark.page, '12-dashboard-escuro', '/', 'Páginas por dia');
console.log('erros no console (escuro):', JSON.stringify(dark.errors));

const phone = await session({ width: 390, height: 844 }, 'light');
await shot(phone.page, '13-celular-dashboard', '/', 'Páginas por dia');
await shot(phone.page, '14-celular-parque', '/parque', 'A797019500624');
console.log('erros no console (celular):', JSON.stringify(phone.errors));

await browser.close();
