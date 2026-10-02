import { expect, test, type Page } from '@playwright/test';

function credentials() {
  const email = process.env.DM_E2E_EMAIL;
  const password = process.env.DM_E2E_PASSWORD;
  const serial = process.env.DM_E2E_SERIAL;
  const total = Number(process.env.DM_E2E_TOTAL);
  if (!email || !password || !serial || !total) throw new Error('global-setup não preparou o usuário E2E');
  return { email, password, serial, total: new Intl.NumberFormat('pt-BR').format(total) };
}

async function login(page: Page) {
  const { email, password } = credentials();
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha').fill(password);
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page).toHaveURL(/\/$/);
}

test('login → dashboard → parque → detalhe do equipamento → sair', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  const { serial, total } = credentials();

  await login(page);
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
  await expect(page.getByText('Ao vivo')).toBeVisible();

  await page.getByRole('navigation', { name: 'Menu principal' }).getByRole('link', { name: 'Equipamentos' }).click();
  await expect(page).toHaveURL(/\/parque$/);
  await page.getByLabel('Pesquisa global').fill(serial);
  const link = page.getByRole('link', { name: serial });
  await expect(link).toBeVisible();
  await link.click();

  await expect(page).toHaveURL(/\/parque\/[0-9a-f-]{36}$/);
  await expect(page.getByText(serial).first()).toBeVisible();
  await expect(page.getByText(total).first()).toBeVisible();
  await expect(page.getByLabel('Você está em')).toContainText('Equipamentos');

  // Recarregar a página mantém a sessão (refresh pelo cookie httpOnly).
  await page.reload();
  await expect(page.getByText(total).first()).toBeVisible();

  expect(consoleErrors).toEqual([]);
});

test('no celular o parque vira cartões com medidor e níveis', async ({ page }) => {
  const { serial, total } = credentials();
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto('/parque');
  await page.getByLabel('Pesquisa global').fill(serial);
  const card = page.getByTestId('park-row').filter({ has: page.getByRole('link', { name: serial }) });
  await expect(card).toContainText(total);
  await expect(card).toContainText('Cliente E2E');
  await expect(card.getByText('8%', { exact: true })).toBeVisible();
  const box = await card.boundingBox();
  expect(box?.width ?? 0).toBeLessThanOrEqual(390);
});

test('rota protegida sem sessão volta para o login', async ({ page }) => {
  await page.goto('/coletores');
  await expect(page).toHaveURL(/\/login$/);
});

test('senha errada mostra o motivo na tela', async ({ page }) => {
  const { email } = credentials();
  await page.goto('/login');
  await page.getByLabel('E-mail').fill(email);
  await page.getByLabel('Senha').fill('senha-errada-de-proposito');
  await page.getByRole('button', { name: 'Entrar' }).click();
  await expect(page.getByRole('alert')).toBeVisible();
});

test('computadores: impressoras USB do PC e leitura manual da que não tem contador', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  await login(page);
  await page.getByRole('navigation', { name: 'Menu principal' }).getByRole('link', { name: 'Computadores' }).click();
  await expect(page).toHaveURL(/\/computadores$/);
  await page.getByLabel('Buscar computador').fill('Coletor E2E');
  const pc = page.getByTestId('computers').getByRole('button').filter({ hasText: 'PC-E2E' });
  await expect(pc).toContainText('2 impressora(s) USB');
  await pc.click();

  const pjl = page.getByRole('row').filter({ hasText: 'E2EUSBPJL01' });
  await expect(pjl).toContainText('automático (PJL)');
  const manual = page.getByRole('row').filter({ hasText: 'USB-E2E000000001' });
  await expect(manual).toContainText('sem contador disponível');

  // Contador sempre maior que o da execução anterior (minutos desde 2026-01-01).
  const total = 1000 + Math.floor((Date.now() - Date.UTC(2026, 0, 1)) / 60_000);
  await manual.getByRole('button', { name: 'Registrar leitura' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Total').fill(String(total));
  await dialog.getByRole('button', { name: 'Registrar' }).click();
  const shown = new Intl.NumberFormat('pt-BR').format(total);
  await expect(page.getByText(`Leitura registrada: total ${shown}`)).toBeVisible();
  await expect(manual).toContainText(shown);

  // Contador menor que o último é recusado com o motivo na tela.
  await manual.getByRole('button', { name: 'Registrar leitura' }).click();
  await dialog.getByLabel('Total').fill('5');
  await dialog.getByRole('button', { name: 'Registrar' }).click();
  await expect(page.getByText(/menor que o da leitura/)).toBeVisible();
  // O único erro esperado no console é o da recusa acima (erros nunca silenciosos: tela + console.error).
  expect(consoleErrors.filter((e) => !e.includes('400') && !e.includes('menor que o da leitura'))).toEqual([]);
});
