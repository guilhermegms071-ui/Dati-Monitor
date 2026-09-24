import { expect, test } from '@playwright/test';

test('portal abre e mostra a API conectada ao banco', async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await page.goto('/');
  await expect(page).toHaveTitle('Dati Monitor');
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Dati Monitor');
  await expect(page.getByRole('status')).toContainText('API: conectada');
  expect(consoleErrors).toEqual([]);
});
