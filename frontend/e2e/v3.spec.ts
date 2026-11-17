import { type APIRequestContext, expect, test } from '@playwright/test'

/**
 * The v3 pages, end to end, along the path a demo walks: find something by
 * search, read the savings, test a decision, count a shelf, log waste, check
 * a delivery in short, and see what autopilot would do. The flags are
 * switched on for this file only and off again after, so the other suites
 * keep running against the app as it is without them.
 */
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }
const FLAGS = ['v3_nav', 'v3_stock', 'v3_receiving', 'v3_autopilot']

async function setFlags(request: APIRequestContext, enabled: boolean) {
  expect((await request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  for (const flag of FLAGS) expect((await request.put(`${API}/api/v1/platform/flags`, { data: { flag, enabled } })).ok()).toBeTruthy()
}

test.beforeAll(async ({ request }) => setFlags(request, true))
test.afterAll(async ({ request }) => setFlags(request, false))

test.beforeEach(async ({ page }) => {
  // The first-visit tour has its own test; everywhere else it would sit on top of the page.
  await page.addInitScript(() => window.localStorage.setItem('fullhouse.tour.seen', '1'))
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
})

test('search finds an item, a supplier and a page, and Enter goes there', async ({ page }) => {
  await page.goto('/#overview')
  await expect(page.getByRole('button', { name: /^Search/ }).first()).toBeVisible() // the shortcut exists once the flag has loaded
  await page.keyboard.press('Control+k')
  const box = page.getByRole('combobox')
  await box.fill('tomato')
  await expect(page.getByRole('option').filter({ hasText: 'Tomatoes, 25 lb case' })).toBeVisible()
  await box.fill('savings')
  await expect(page.getByRole('option').filter({ hasText: 'Savings & costs' })).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/#insights$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Savings & costs' })).toBeVisible()
})

test('the tour shows once, can be skipped, and Help brings it back', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.removeItem('fullhouse.tour.seen'))
  await page.goto('/#overview')
  const tour = page.getByRole('dialog', { name: 'Start with the digest' })
  await expect(tour).toBeVisible()
  await tour.getByRole('button', { name: 'Skip' }).click()
  await expect(tour).toHaveCount(0)
})

test('savings and costs: every chart has a table, and spend can be split two ways', async ({ page }) => {
  await page.goto('/#insights')
  await expect(page.getByRole('heading', { name: 'Is it paying for itself?' })).toBeVisible()
  await page.getByRole('group', { name: 'Savings: chart or table' }).getByRole('button', { name: 'Table' }).click()
  await expect(page.getByRole('columnheader', { name: 'Found to date' })).toBeVisible()
  await page.getByRole('button', { name: 'By category' }).click()
  await expect(page.getByLabel('This period against the one before').getByText('Food')).toBeVisible()
})

test('test a decision: the supplier switch says what a year of it is worth', async ({ page }) => {
  await page.goto('/#tools')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Test a decision before you make it')
  await page.getByRole('group', { name: 'Tool' }).getByRole('button', { name: 'Switch supplier' }).click()
  await expect(page.getByText(/\$[\d,]+ a year/).first()).toBeVisible()
  await page.getByRole('group', { name: 'Tool' }).getByRole('button', { name: 'Menu price' }).click()
  await expect(page.getByText('Profit a plate')).toBeVisible()
})

test('count a shelf on a phone: enter, post, and the stock figure is the count', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#count')
  await page.getByRole('button', { name: /^Everything/ }).click()
  const row = page.getByRole('listitem').filter({ hasText: 'Flour' }).first()
  await row.getByRole('spinbutton').fill('7')
  await expect(page.getByRole('status').filter({ hasText: 'counted' })).toContainText('1 of')
  await expect(page.getByRole('status').filter({ hasText: 'counted' })).toContainText('saved')
  await page.getByRole('button', { name: 'Post count' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Post count' }).click()
  await expect(page.getByText(/^Posted/)).toBeVisible()
  const items = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  expect(items.find((i: { name: string }) => i.name === 'Flour').quantity_on_hand).toBe(7)
})

test('log waste in three taps and it shows in the log and comes off stock', async ({ page }) => {
  await page.goto('/#variance')
  await page.getByRole('button', { name: 'Log waste' }).first().click()
  const dialog = page.getByRole('dialog', { name: 'Log waste' })
  await dialog.getByLabel('Item').selectOption({ label: await dialog.getByLabel('Item').locator('option', { hasText: 'Flour' }).innerText() })
  await dialog.getByLabel(/How much/).fill('2')
  await dialog.getByRole('button', { name: 'Dropped' }).click()
  await dialog.getByRole('button', { name: 'Log waste' }).click()
  await expect(page.getByText('2 lb of Flour').first()).toBeVisible()
  await expect(page.getByText('Dropped or spilled').first()).toBeVisible()
})

test('a delivery checked in short updates stock, closes the order and rings the bell', async ({ page }) => {
  const items = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  const item = items.find((i: { name: string }) => i.name === 'Mozzarella') ?? items[0]
  const placed = await page.request.post(`${API}/api/v1/restaurant/purchase-orders`, { data: { inventory_item_id: item.id, quantity: 10 } })
  expect(placed.ok()).toBeTruthy()

  await page.goto('/#receive')
  const order = page.getByRole('listitem').filter({ hasText: `10 ${item.unit} of ${item.name}` })
  await order.getByRole('button', { name: 'Check in' }).click()
  await page.getByRole('button', { name: `One less ${item.name}` }).click()
  await page.getByRole('button', { name: 'Check in with 1 difference' }).click()
  await expect(page.getByText(/1 line short/).first()).toBeVisible()

  const after = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  expect(after.find((i: { id: string }) => i.id === item.id).quantity_on_hand).toBe(item.quantity_on_hand + 9)
  await page.goto('/#receive')
  await expect(page.getByRole('listitem').filter({ hasText: `10 ${item.unit} of ${item.name}` })).toHaveCount(0)
  await page.getByRole('button', { name: /Notifications/ }).click()
  await expect(page.getByRole('dialog', { name: 'Notifications' }).getByText(/delivery came in short/)).toBeVisible()
})

test('autopilot is off until an owner switches it on, takes one plain order address, and can preview its limits', async ({ page }) => {
  await page.goto('/#autopilot')
  await expect(page.getByRole('heading', { level: 1, name: 'Ordering autopilot' })).toBeVisible()
  await expect(page.getByText('Nothing waiting to send')).toBeVisible()
  const supplier = page.getByRole('listitem').filter({ hasText: 'Green Valley Farms' }).first()
  await expect(supplier.getByText('You send')).toBeVisible()
  await supplier.getByRole('button', { name: 'Edit' }).click()
  await supplier.getByLabel('Order address').fill('orders@gvf.example.com, me@evil.example')
  await supplier.getByRole('button', { name: 'Save order days' }).click()
  await expect(page.getByText(/one email address/i)).toBeVisible()
  await supplier.getByLabel('Order address').fill('orders@gvf.example.com')
  await supplier.getByRole('button', { name: 'Save order days' }).click()
  await expect(page.getByText('Order days saved')).toBeVisible()
  await supplier.getByRole('button', { name: 'Try these on the last 8 weeks' }).click()
  await expect(supplier.getByText(/of 8 orders would have gone by themselves/)).toBeVisible()
})
