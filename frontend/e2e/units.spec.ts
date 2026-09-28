import { expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

test('an item bought by the case is delivered in cases and counted in units', async ({ page }) => {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  const name = `Oat milk ${Date.now()}`

  await page.goto('/#inventory')
  await page.getByRole('button', { name: 'Add item' }).click()
  const add = page.getByRole('form', { name: 'Add an item' })
  await add.getByLabel('Name').fill(name)
  await add.getByRole('combobox', { name: /^Unit/ }).selectOption('each')
  await add.getByLabel('On hand now').fill('4')
  await add.getByLabel('Reorder at').fill('6')
  await add.getByRole('combobox', { name: /^Bought by the/ }).selectOption('case')
  await add.getByLabel('each per case').fill('6')
  await add.getByRole('button', { name: 'Add item' }).click()
  await expect(page.getByText('Item added')).toBeVisible()

  const row = page.getByRole('row').filter({ hasText: name }).first()
  await page.getByRole('button', { name: `Record a stock change for ${name}` }).click()
  const change = page.getByRole('form', { name: `Stock change for ${name}` })
  await change.getByRole('button', { name: 'In cases of 6' }).click()
  await change.getByLabel('Amount received (cases)').fill('3')
  await expect(change).toContainText('3 × 6 = 18 each')
  await change.getByRole('button', { name: 'Save change' }).click()

  await expect(row).toContainText('22 each')
  await expect(row).toContainText('3.7 cases')
  await page.getByRole('button', { name: `History of ${name}` }).click()
  await expect(page.getByRole('list', { name: 'Stock changes' })).toContainText('3 cases of 6 each')
})
