// Fluxo manual da Fase 7 (fora da suíte): telas novas com o banco de desenvolvimento.
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

await shot('f7-01-dashboard', '/', 'Produção do mês');
await shot('f7-02-relatorio-producao', '/relatorios?r=production', 'Páginas PB e cor');
const [pdf] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'PDF' }).click()]);
await pdf.saveAs(`${out}/f7-producao.pdf`);
const [xlsx] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'XLSX' }).click()]);
await xlsx.saveAs(`${out}/f7-producao.xlsx`);
console.log('exportados:', pdf.suggestedFilename(), xlsx.suggestedFilename());
await shot('f7-03-contador-diario', '/relatorios?r=daily_counter', 'Páginas por dia com gráfico');
await shot('f7-04-cobranca', '/relatorios?r=billing', 'franquia e excedente');
await shot('f7-05-status-coletores', '/relatorios?r=agent_status', 'Estado, versão');
await shot('f7-06-perfis', '/perfis', 'konica-minolta');
await shot('f7-07-perfil-konica', '/perfis/konica-minolta', 'Editor YAML');
await shot('f7-08-integracao', '/integracao', 'Parâmetros da empresa');
await page.getByRole('tab', { name: 'Fila de envio' }).click();
await page.waitForTimeout(1200);
await page.screenshot({ path: `${out}/f7-09-fila-erp.png`, fullPage: true });
const tiles = [];
page.on('response', (r) => {
  if (r.url().includes('tile.openstreetmap.org')) tiles.push(r.status());
});
page.on('requestfailed', (r) => {
  if (r.url().includes('tile.openstreetmap.org')) tiles.push(`falhou: ${r.failure()?.errorText ?? '?'}`);
});
await page.goto(`${base}/mapa`);
await page.locator('.leaflet-tile-loaded').first().waitFor({ timeout: 20_000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/f7-10-mapa.png`, fullPage: true });
console.log('quadros do mapa:', JSON.stringify(tiles.slice(0, 8)));
await shot('f7-11-clientes', '/clientes', 'Importar CSV');
await page.getByRole('button', { name: 'Importar CSV' }).click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/f7-12-importar-csv.png` });
console.log('erros no console:', JSON.stringify(errors));
await browser.close();
