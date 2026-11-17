import { readFileSync } from 'node:fs'
import { type APIRequestContext, expect, test } from '@playwright/test'

/**
 * Margin & Labor Engine, the shared data: with a module switched on, Connections offers the order-level
 * import, and sending the same export twice leaves the same rows. The flags are on for this file only.
 */
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }
const FLAGS = ['v2_connect', 'ml_menu_costs']
const ORDERS = new URL('../../backend/tests/fixtures_ml/orders.csv', import.meta.url).pathname

async function setFlags(request: APIRequestContext, enabled: boolean) {
  expect((await request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  for (const flag of FLAGS) expect((await request.put(`${API}/api/v1/platform/flags`, { data: { flag, enabled } })).ok()).toBeTruthy()
}

test.beforeAll(async ({ request }) => setFlags(request, true))
test.afterAll(async ({ request }) => setFlags(request, false))

test('orders import from a CSV on Connections, and the same file again changes nothing', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('fullhouse.tour.seen', '1'))
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  await page.goto('/#connect')
  const card = page.getByRole('region', { name: 'Order-level data' })
  await expect(card).toBeVisible()
  const file = { name: 'orders.csv', mimeType: 'text/csv', buffer: readFileSync(ORDERS) }

  await card.locator('input[type=file]').first().setInputFiles(file)
  await expect(card.getByText('10 rows read: 5 new, 0 changed, 0 the same as before.')).toBeVisible()
  await expect(card.getByText('2 rows were left out. Fix them in the file and import it again.')).toBeVisible()
  await expect(card.getByText('Line 11: No order id.')).toBeVisible()
  await expect(card.getByText(/5 orders over 2 days/)).toBeVisible()

  await card.locator('input[type=file]').first().setInputFiles(file)
  await expect(card.getByText('10 rows read: 0 new, 0 changed, 5 the same as before.')).toBeVisible()
  await expect(card.getByText(/5 orders over 2 days/)).toBeVisible()
})
