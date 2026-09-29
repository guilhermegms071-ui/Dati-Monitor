import { spawn, execFileSync, type ChildProcess } from 'node:child_process';
import { createWriteStream, existsSync, mkdirSync, rmSync } from 'node:fs';

import { expect, test, type Page } from '@playwright/test';

import { repoRoot } from './paths';

/**
 * Fluxo da seção 13 do PROMPT com o dm-agent real (compilado do código): portal gera o código →
 * agente se cadastra → aparece online → faixa de IP configurada pelo portal → varredura pelo botão →
 * as 8 impressoras simuladas aparecem no parque com contadores PB/cor e níveis → detalhe com gráfico.
 * O coletor roda sob o dm-watchdog real (modo processo): o portal mostra o vigia ativo e o comando
 * "Reiniciar o coletor (pelo watchdog)" é executado por ele (seção 5.1).
 * (Alerta de offline por e-mail e página web pelo túnel entram nas Fases 6 e 7.)
 */

const win = process.platform === 'win32';
const workDir = `${repoRoot}var/e2e`;
const agentExe = `${workDir}/dm-agent${win ? '.exe' : ''}`;
const watchdogExe = `${workDir}/dm-watchdog${win ? '.exe' : ''}`;
const dataDir = `${workDir}/agent-data`;
const AGENT_NAME = 'Coletor E2E real';
const SIM_PORTS = Array.from({ length: 8 }, (_, i) => 12161 + i);

let watchdog: ChildProcess | null = null;

function goExe(): string {
  if (process.env.GO_EXE) return process.env.GO_EXE;
  const winGo = 'C:/Program Files/Go/bin/go.exe';
  return win && existsSync(winGo) ? winGo : 'go';
}

function env(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`global-setup não definiu ${name}`);
  return v;
}

async function login(page: Page) {
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(env('DM_E2E_EMAIL'));
  await page.getByLabel('Senha').fill(env('DM_E2E_PASSWORD'));
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/$/);
}

/** Envia um comando pelo menu "Comandos" do coletor e espera o resultado na janela do comando. */
async function runCommand(page: Page, label: string, timeout: number) {
  await page.getByRole('button', { name: 'Comandos' }).click();
  await page.getByRole('menuitem', { name: label }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Concluído', { exact: true })).toBeVisible({ timeout });
  const text = (await dialog.textContent()) ?? '';
  await page.keyboard.press('Escape');
  return text;
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(() => {
  mkdirSync(workDir, { recursive: true });
  rmSync(dataDir, { recursive: true, force: true });
  for (const [exe, pkg] of [
    [agentExe, './cmd/dm-agent'],
    [watchdogExe, './cmd/dm-watchdog'],
  ]) {
    execFileSync(goExe(), ['build', '-o', exe, pkg], { cwd: `${repoRoot}agent`, stdio: 'inherit' });
  }
});

/** Encerra o watchdog E o coletor filho dele (a árvore toda). */
function killTree(child: ChildProcess | null) {
  if (!child?.pid) return;
  try {
    if (win) execFileSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    else process.kill(-child.pid, 'SIGKILL');
  } catch (err) {
    console.error('Falha ao encerrar o watchdog do E2E:', err);
  }
}

test.afterAll(() => {
  killTree(watchdog);
  watchdog = null;
});

test('coletor real: cadastro pelo portal, online, varredura e parque com contadores e níveis', async ({ page }) => {
  test.setTimeout(300_000);
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  await login(page);

  // 1. Novo coletor pelo portal → código de cadastro.
  await page.getByRole('navigation', { name: 'Menu principal' }).getByRole('link', { name: 'Coletores' }).click();
  await page.getByRole('button', { name: 'Novo coletor' }).click();
  const dialog = page.getByRole('dialog');
  // Seletores com busca no servidor (nada de lista inteira no navegador).
  await dialog.getByLabel('Cliente').fill(env('DM_E2E_CUSTOMER'));
  await page.getByRole('option', { name: env('DM_E2E_CUSTOMER'), exact: true }).click();
  await dialog.getByLabel('Local').fill(env('DM_E2E_REAL_SITE_NAME'));
  await page.getByRole('option', { name: env('DM_E2E_REAL_SITE_NAME'), exact: true }).click();
  await dialog.getByLabel('Nome do coletor').fill(AGENT_NAME);
  await dialog.getByRole('button', { name: 'Gerar código' }).click();
  const code = ((await dialog.getByTestId('enrollment-code').textContent()) ?? '').trim();
  expect(code).toMatch(/^[A-Z0-9]{8}$/);
  await dialog.getByRole('button', { name: 'Fechar' }).first().click();

  // 2. O agente real se cadastra com o código e passa a rodar, iniciado e vigiado pelo dm-watchdog.
  execFileSync(
    agentExe,
    [
      'enroll',
      '--server',
      'http://127.0.0.1:8000',
      '--code',
      code,
      '--data-dir',
      dataDir,
      '--health-addr',
      '127.0.0.1:47790',
    ],
    { stdio: 'inherit' },
  );
  const started = Date.now();
  const log = createWriteStream(`${workDir}/agent.log`);
  watchdog = spawn(
    watchdogExe,
    [
      'run',
      '--data-dir',
      dataDir,
      '--agent-exe',
      agentExe,
      '--health-addr',
      '127.0.0.1:47792',
      '--check-every',
      '3s',
      '--report-every',
      '5s',
      '--start-grace',
      '10s',
    ],
    { stdio: ['ignore', 'pipe', 'pipe'], detached: !win },
  );
  watchdog.stdout?.pipe(log);
  watchdog.stderr?.pipe(log);

  // 3. Aparece online no portal em menos de 10 s (critério 2 da seção 15).
  await page.getByRole('link', { name: AGENT_NAME }).first().click();
  await expect(page).toHaveURL(/\/coletores\/[0-9a-f-]{36}$/);
  await expect(page.getByText('Online').first()).toBeVisible({ timeout: 10_000 });
  expect(Date.now() - started).toBeLessThan(10_000);
  // O vigia se comunica com o servidor pelo canal próprio e aparece ativo (aba Saúde).
  await expect(page.getByText('Vigia (dm-watchdog)')).toBeVisible();
  await expect(page.getByText(/^ativo/).first()).toBeVisible({ timeout: 30_000 });

  // 4. Faixa de IP das impressoras simuladas, aplicada e varrida pelos comandos ao vivo.
  await page.getByRole('tab', { name: 'Faixas de IP' }).click();
  await page.getByLabel('Faixa, IP ou hostname').fill('127.0.0.1/32');
  await page.getByLabel('Portas SNMP').fill(SIM_PORTS.join(', '));
  await page.getByRole('button', { name: 'Adicionar' }).click();
  await expect(page.getByText('Adicionado', { exact: true })).toBeVisible();
  const applied = await runCommand(page, 'Aplicar configuração', 60_000);
  expect(applied).toContain('"ranges": 1');
  const scan = await runCommand(page, 'Varrer agora', 180_000);
  expect(scan).toContain('"printers_found": 8');

  // 5. Descobertas: as 8 impressoras chegam pendentes (o local não ativa sozinho) e são ativadas em lote.
  await page.goto('/descobertas');
  const pending = page.getByRole('row').filter({ hasText: env('DM_E2E_REAL_SITE_NAME') });
  await expect(pending).toHaveCount(8, { timeout: 120_000 });
  for (const row of await pending.all()) await row.getByRole('checkbox').click();
  await page.getByRole('button', { name: 'Ativar selecionados' }).click();
  await expect(page.getByText(/^8 equipamento\(s\) ativado\(s\)/)).toBeVisible();
  await expect(pending).toHaveCount(0);

  // 6. Parque do local: as 8 impressoras com contadores e níveis (leituras chegam pela fila do agente).
  await page.goto(`/parque?site=${env('DM_E2E_REAL_SITE')}`);
  const rows = page.getByTestId('park-row');
  await expect(rows).toHaveCount(8, { timeout: 120_000 });
  const konica = rows.filter({ has: page.getByRole('link', { name: 'A797019500624' }) });
  await expect(konica).toContainText('217.031', { timeout: 120_000 });
  await expect(konica).toContainText('PB: 100.150 CL: 116.881');
  await expect(konica).toContainText(AGENT_NAME);
  for (const pct of ['25%', '55%', '66%', '5%']) await expect(konica.getByText(pct, { exact: true })).toBeVisible();

  // 7. Detalhe do equipamento com gráfico de contadores e atributos da leitura diária.
  await konica.getByRole('link', { name: 'A797019500624' }).click();
  await expect(page).toHaveURL(/\/parque\/[0-9a-f-]{36}$/);
  await expect(page.getByText('217.031').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Contadores' }).click();
  await expect(page.locator('.recharts-surface').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Atributos' }).click();
  await expect(page.getByText('Controller 1.20')).toBeVisible();
  await expect(page.getByText('HDD: ')).toBeVisible();

  // 8. Reiniciar o coletor pelo watchdog: comando entregue no canal do vigia, coletor volta saudável.
  await page.goto('/coletores');
  await page.getByRole('link', { name: AGENT_NAME }).first().click();
  const restarted = await runCommand(page, 'Reiniciar o coletor (pelo watchdog)', 90_000);
  expect(restarted).toContain('"restarted": true');

  expect(consoleErrors).toEqual([]);
});
