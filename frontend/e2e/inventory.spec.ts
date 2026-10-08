import { type Page, expect, test } from '@playwright/test'
import { joinAsMember } from './team'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

const row = (page: Page, name: string) => page.getByRole('row').filter({ hasText: name }).first()

test('coworkers add stock and record changes, and each sees the other’s', async ({ page, browser }) => {
  const item = `Saffron ${Date.now()}`

  // --- the owner adds an item
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  await page.goto('/#inventory')
  await page.getByRole('button', { name: 'Add item' }).click()
  const add = page.getByRole('form', { name: 'Add an item' })
  await add.getByLabel('Name').fill(item)
  await add.getByRole('combobox', { name: /^Unit/ }).selectOption('g')
  await add.getByLabel('On hand now').fill('10')
  await add.getByLabel('Reorder at').fill('5')
  await add.getByRole('button', { name: 'Add item' }).click()
  await expect(page.getByText('Item added')).toBeVisible()
  await expect(row(page, item)).toContainText('10 g')
  await expect(row(page, item).getByText('Low')).toHaveCount(0)

  // Using more than is there is caught before anything is sent.
  await page.getByRole('button', { name: `Record a stock change for ${item}` }).click()
  const change = page.getByRole('form', { name: `Stock change for ${item}` })
  await change.getByRole('button', { name: 'Used' }).click()
  await change.getByLabel('Amount used (g)').fill('50')
  await change.getByRole('button', { name: 'Save change' }).click()
  await expect(change.getByRole('alert')).toContainText('Only 10 g is on hand')
  await change.getByRole('button', { name: 'Cancel' }).click()
  await expect(row(page, item)).toContainText('10 g')

  // --- a coworker, in their own browser, joins and logs a delivery
  const coworkerContext = await browser.newContext()
  const coworker = await coworkerContext.newPage()
  await joinAsMember(coworker.request, { name: 'Carla Cook', email: `carla-${Date.now()}@example.com` })
  await coworker.goto('/#inventory')
  await expect(row(coworker, item)).toContainText('10 g') // the owner's item, from the database
  await expect(coworker.getByRole('button', { name: `Edit ${item}` })).toHaveCount(0) // details are for owners and admins

  await coworker.getByRole('button', { name: `Record a stock change for ${item}` }).click()
  const delivery = coworker.getByRole('form', { name: `Stock change for ${item}` })
  await delivery.getByLabel('Amount received (g)').fill('15')
  await delivery.getByLabel('Note').fill('Invoice 42')
  await expect(delivery).toContainText('→ 25 g after this')
  await delivery.getByRole('button', { name: 'Save change' }).click()
  await expect(row(coworker, item)).toContainText('25 g')
  const history = coworker.getByRole('list', { name: 'Stock changes' })
  await expect(history.getByRole('listitem').first()).toContainText('+15 g')
  await expect(history.getByRole('listitem').first()).toContainText('Delivery received')
  await expect(history.getByRole('listitem').first()).toContainText('Carla Cook')

  // --- the owner refreshes and sees the coworker's delivery, and who logged it
  await page.reload()
  await expect(row(page, item)).toContainText('25 g')
  await page.getByRole('button', { name: `History of ${item}` }).click()
  await expect(page.getByRole('heading', { name: `History of ${item}` })).toBeVisible()
  const ownerHistory = page.getByRole('list', { name: 'Stock changes' }).getByRole('listitem')
  await expect(ownerHistory).toHaveCount(2)
  await expect(ownerHistory.first()).toContainText('Carla Cook · now 25 g · “Invoice 42”')
  await expect(ownerHistory.last()).toContainText('Starting stock')

  // --- a stock count below the reorder point marks it low, for both of them
  await page.getByRole('button', { name: `Record a stock change for ${item}` }).click()
  const count = page.getByRole('form', { name: `Stock change for ${item}` })
  await count.getByRole('button', { name: 'Stock count' }).click()
  await count.getByLabel('Counted on hand (g)').fill('3')
  await count.getByRole('button', { name: 'Save change' }).click()
  await expect(row(page, item)).toContainText('3 g')
  await expect(row(page, item).getByText('Low')).toBeVisible()
  await expect(ownerHistory.first()).toContainText('−22 g')

  await coworker.reload()
  await expect(row(coworker, item)).toContainText('3 g')
  await expect(row(coworker, item).getByText('Low')).toBeVisible()
  await coworkerContext.close()
})
