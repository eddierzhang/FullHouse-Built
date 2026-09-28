import { type Page, expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

// A phone: the day's tasks from a bottom bar, readable cards instead of wide tables, big tap targets.
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

async function noSidewaysScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(1)
}

test('on a phone, daily tasks are one tap away and stock can be logged', async ({ page }) => {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  const name = `Thyme ${Date.now()}`
  expect(
    (await page.request.post(`${API}/api/v1/restaurant/inventory-items`, {
      data: { name, unit: 'bunch', quantity_on_hand: 4, reorder_threshold: 2 },
    })).ok(),
  ).toBeTruthy()

  await page.goto('/#overview')
  const tabs = page.getByRole('navigation', { name: 'Daily tasks' })
  await expect(tabs).toBeVisible()
  await expect(tabs.getByRole('link')).toHaveText([/Today/, /Inventory/, /Schedule/, /Recommend/])
  await noSidewaysScroll(page)

  // Each page in the bar opens and fits the screen.
  for (const [tab, heading] of [['Schedule', 'Schedule & staff'], ['Recommend', 'Recommendations'], ['Inventory', 'Inventory']]) {
    await tabs.getByRole('link', { name: new RegExp(`^${tab}`) }).click()
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await noSidewaysScroll(page)
  }

  // The inventory table reads as cards: each value labelled, nothing off-screen.
  const row = page.getByRole('row').filter({ hasText: name }).first()
  await expect(row).toBeVisible()
  const box = await row.boundingBox()
  expect(box!.x + box!.width).toBeLessThanOrEqual(390)

  // Log a delivery with a thumb: buttons are big enough to hit.
  const open = page.getByRole('button', { name: `Record a stock change for ${name}` })
  expect((await open.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await open.tap()
  const form = page.getByRole('form', { name: `Stock change for ${name}` })
  await form.getByRole('button', { name: 'Delivery' }).tap()
  await form.getByLabel(/^Amount/).fill('3')
  const save = form.getByRole('button', { name: 'Save change' })
  expect((await save.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await save.tap()
  await expect(row).toContainText('7 bunch')

  // Everything else is behind More.
  await tabs.getByRole('button', { name: 'More' }).tap()
  await expect(page.getByRole('link', { name: 'History & undo' })).toBeVisible()
})

test('on a phone, going offline says so plainly', async ({ page, context }) => {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  await page.goto('/#inventory')
  await expect(page.getByRole('heading', { level: 1, name: 'Inventory' })).toBeVisible()

  await context.setOffline(true)
  await expect(page.getByRole('alert').filter({ hasText: 'You’re offline' })).toBeVisible()
  await context.setOffline(false)
  await expect(page.getByRole('alert').filter({ hasText: 'You’re offline' })).toHaveCount(0)
})
