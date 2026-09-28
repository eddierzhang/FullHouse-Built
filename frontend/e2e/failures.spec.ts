import { type Page, expect, test } from '@playwright/test'

// Realistic ways a save goes wrong: the connection drops, the server fails,
// the login expires. Each must leave stock right and tell the person so.

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

const row = (page: Page, name: string) => page.getByRole('row').filter({ hasText: name }).first()

async function withItem(page: Page): Promise<{ id: string; name: string }> {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  const name = `Cumin ${Date.now()}`
  const created = await page.request.post(`${API}/api/v1/restaurant/inventory-items`, {
    data: { name, unit: 'g', quantity_on_hand: 10, reorder_threshold: 2 },
  })
  expect(created.ok()).toBeTruthy()
  await page.goto('/#inventory')
  await expect(row(page, name)).toContainText('10 g')
  return { id: (await created.json()).id, name }
}

async function logDelivery(page: Page, name: string, amount: string) {
  const form = page.getByRole('form', { name: `Stock change for ${name}` })
  if (!(await form.isVisible())) await page.getByRole('button', { name: `Record a stock change for ${name}` }).click()
  await form.getByRole('button', { name: 'Delivery' }).click()
  await form.getByLabel(/^Amount/).fill(amount)
  await form.getByRole('button', { name: 'Save change' }).click()
  return form
}

async function deliveries(page: Page, id: string): Promise<number> {
  const history = await page.request.get(`${API}/api/v1/restaurant/inventory-adjustments`, {
    params: { inventory_item_id: id },
  })
  return (await history.json()).filter((h: { reason: string }) => h.reason === 'delivery').length
}

test('a delivery saved just as the connection drops is recorded once', async ({ page }) => {
  const item = await withItem(page)

  // The server records it, but the answer never reaches the browser.
  let dropped = false
  await page.route('**/adjustments', async (route) => {
    if (dropped) return route.continue()
    dropped = true
    await route.fetch() // reaches the server...
    await route.abort('connectionreset') // ...but the reply is lost
  })

  await logDelivery(page, item.name, '5')
  // The browser retries with the same request id; the server answers with the first save.
  await expect(row(page, item.name)).toContainText('15 g')
  expect(await deliveries(page, item.id)).toBe(1)
})

test('offline: the save fails clearly, and trying again once back online counts it once', async ({ page, context }) => {
  const item = await withItem(page)

  await context.setOffline(true)
  const form = await logDelivery(page, item.name, '4')
  await expect(form.getByRole('alert')).toContainText('connection')
  await context.setOffline(false)

  await form.getByRole('button', { name: 'Save change' }).click()
  await expect(row(page, item.name)).toContainText('14 g')
  expect(await deliveries(page, item.id)).toBe(1)
})

test('a failed save says so and changes nothing', async ({ page }) => {
  const item = await withItem(page)
  await page.route('**/adjustments', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'Internal Server Error' }) }),
  )

  const form = await logDelivery(page, item.name, '3')
  await expect(form.getByRole('alert')).toBeVisible()
  await page.unroute('**/adjustments')
  await page.reload()
  await expect(row(page, item.name)).toContainText('10 g')
  expect(await deliveries(page, item.id)).toBe(0)
})

test('an expired login goes back to the sign-in page instead of failing silently', async ({ page, context }) => {
  const item = await withItem(page)
  await context.clearCookies() // the session is gone, as after it expires

  await logDelivery(page, item.name, '2')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  // And says why, so nobody thinks they signed themselves out or lost work.
  await expect(page.getByText('You’ve been signed out')).toBeVisible()
  await expect(page.getByText('Everything you’d already saved is kept.')).toBeVisible()
})
