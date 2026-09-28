import { type Page, expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

async function signedIn(page: Page) {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
}

async function addItem(page: Page, name: string) {
  const created = await page.request.post(`${API}/api/v1/restaurant/inventory-items`, {
    data: { name, unit: 'g', quantity_on_hand: 10, reorder_threshold: 2 },
  })
  expect(created.ok()).toBeTruthy()
  return (await created.json()).id as string
}

test('a mistake shows in History in plain words, and Undo puts it back', async ({ page }) => {
  await signedIn(page)
  const name = `Fennel ${Date.now()}`
  const id = await addItem(page, name)
  const edit = await page.request.patch(`${API}/api/v1/restaurant/inventory-items/${id}`, { data: { reorder_threshold: 9 } })
  expect(edit.ok()).toBeTruthy()

  await page.goto('/#history')
  await expect(page.getByRole('heading', { level: 1, name: 'History & undo' })).toBeVisible()
  const entry = page.getByRole('listitem').filter({ hasText: `changed ${name}` })
  await expect(entry).toContainText('reorder at: 2 → 9')

  page.once('dialog', (dialog) => dialog.accept())
  await entry.getByRole('button', { name: 'Undo' }).click()
  await expect(page.getByText('Undone')).toBeVisible()

  const items = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  expect(items.find((i: { id: string }) => i.id === id).reorder_threshold).toBe(2)
  // The undo is itself on record.
  await expect(page.getByRole('listitem').filter({ hasText: 'undo' }).first()).toBeVisible()
})

test('undoing something that has changed since asks first, then can undo anyway', async ({ page }) => {
  await signedIn(page)
  const name = `Sumac ${Date.now()}`
  const id = await addItem(page, name)
  const item = `${API}/api/v1/restaurant/inventory-items/${id}`
  await page.request.patch(item, { data: { reorder_threshold: 5 } })
  await page.request.patch(item, { data: { reorder_threshold: 7 } }) // a later change on top

  await page.goto('/#history')
  const first = page.getByRole('listitem').filter({ hasText: 'reorder at: 2 → 5' })
  page.once('dialog', (dialog) => dialog.accept())
  await first.getByRole('button', { name: 'Undo' }).click()
  await expect(first.getByRole('alert')).toBeVisible()
  await first.getByRole('button', { name: 'Undo anyway' }).click()
  await expect(page.getByText('Undone')).toBeVisible()
  expect((await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()).find((i: { id: string }) => i.id === id).reorder_threshold).toBe(2)
})

test('a teammate’s change shows up without anyone refreshing', async ({ page, browser }) => {
  await signedIn(page)
  const name = `Cardamom ${Date.now()}`
  const id = await addItem(page, name)
  await page.goto('/#inventory')
  const row = page.getByRole('row').filter({ hasText: name }).first()
  await expect(row).toContainText('10 g')

  // A coworker, elsewhere, logs a delivery.
  const coworker = await browser.newContext()
  const request = coworker.request
  expect((await request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  const delivery = await request.post(`${API}/api/v1/restaurant/inventory-items/${id}/adjustments`, {
    data: { reason: 'delivery', quantity: 6 },
  })
  expect(delivery.ok()).toBeTruthy()
  await coworker.close()

  // The open page catches up by itself.
  await expect(row).toContainText('16 g', { timeout: 20_000 })
  await expect(page.getByText(/Updated/)).toBeVisible()
})
