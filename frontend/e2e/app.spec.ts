import { type Page, expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

/** Fails the test on any uncaught error or console error in the page. */
function watchForErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  return errors
}

async function stockOf(page: Page, item: string): Promise<number> {
  const items = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  return items.find((i: { name: string }) => i.name === item).quantity_on_hand
}

test.describe('navigation', () => {
  test('every page loads without errors, in the documented order', async ({ page }) => {
    const errors = watchForErrors(page)
    await page.goto('/')

    const nav = page.getByRole('navigation')
    const labels = await nav.getByRole('link').allInnerTexts()
    const names = labels.map((l) => l.replace(/\d+$/, '').trim())
    expect(names).toEqual([
      'Overview',
      'Approvals',
      'Runs',
      'Agents & schedules',
      'Inventory',
      'Suppliers',
      'Menu',
      'Staff',
      'Profit & forecast',
    ])

    const headings: Record<string, string> = {
      Approvals: 'Approvals',
      Runs: 'Runs',
      'Agents & schedules': 'Agents & schedules',
      Inventory: 'Inventory',
      Suppliers: 'Suppliers',
      Menu: 'Menu',
      Staff: 'Staff',
      'Profit & forecast': 'Profit & forecast',
    }
    for (const [link, heading] of Object.entries(headings)) {
      await nav.getByRole('link', { name: new RegExp(`^${link}`) }).click()
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
      await expect(nav.getByRole('link', { name: new RegExp(`^${link}`) })).toHaveAttribute('aria-current', 'page')
    }

    expect(errors).toEqual([])
  })

  test('old links still land on the right page', async ({ page }) => {
    await page.goto('/#marketing')
    await expect(page.getByRole('heading', { level: 1, name: 'Menu' })).toBeVisible()
    await page.goto('/#employees')
    await expect(page.getByRole('heading', { level: 1, name: 'Staff' })).toBeVisible()
  })
})

test('approving a reorder restocks, and reverting it puts the stock back', async ({ page }) => {
  const before = await stockOf(page, 'Tomatoes')
  await page.goto('/#approvals')

  const row = page.getByRole('listitem').filter({ hasText: 'Reorder 25 × Tomatoes' })
  await row.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByText('Approved', { exact: true })).toBeVisible()
  await expect(row).toHaveCount(0)

  await expect.poll(() => stockOf(page, 'Tomatoes')).toBe(before + 25)
  await page.goto('/#inventory')
  await expect(page.getByRole('row').filter({ hasText: 'Tomatoes' })).toContainText(`${before + 25} lb`)

  // Undo through the change log, then confirm the UI reflects it.
  const changes = await (
    await page.request.get(`${API}/api/v1/changes`, { params: { entity_type: 'inventory_items', operation: 'update' } })
  ).json()
  const restock = changes.items.find((c: { after: { quantity_on_hand?: number } }) => c.after.quantity_on_hand === before + 25)
  expect((await page.request.post(`${API}/api/v1/changes/${restock.id}/revert`)).ok()).toBeTruthy()

  await expect.poll(() => stockOf(page, 'Tomatoes')).toBe(before)
  await page.reload()
  await expect(page.getByRole('row').filter({ hasText: 'Tomatoes' })).toContainText(`${before} lb`)
})

test('a promotion without a price cannot be approved until one is entered', async ({ page }) => {
  await page.goto('/#approvals')
  const row = page.getByRole('listitem').filter({ hasText: 'Garlic Bread' })
  const approve = row.getByRole('button', { name: 'Approve' })

  await expect(approve).toBeDisabled()
  await row.getByLabel('New price').fill('4.50')
  await expect(approve).toBeEnabled()
  await approve.click()
  await expect(page.getByText(/Promotion applied/)).toBeVisible()

  await page.goto('/#menu')
  const dish = page.getByRole('row').filter({ hasText: 'Garlic Bread' })
  await expect(dish).toContainText('$4.50')
  await expect(dish).toContainText('Promo')
})

test('a new task streams live and finishes', async ({ page }) => {
  await page.goto('/#overview')
  await page.getByRole('button', { name: 'New task' }).click()

  const dialog = page.getByRole('dialog', { name: 'New agent task' })
  await dialog.getByLabel('Agent').selectOption({ label: 'Inventory Agent' })
  await dialog.getByLabel('What should it do?').fill('Check what is running low')
  await dialog.getByRole('button', { name: 'Start task' }).click()

  await expect(page.getByRole('heading', { level: 1, name: 'Runs' })).toBeVisible()
  const log = page.getByTestId('run-log')
  await expect(log).toContainText('tool call')
  await expect(log).toContainText('list_low_stock_items')
  await expect(page.getByText('Summary', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /Inventory Agent/ }).first()).toContainText('succeeded')
})

test('scheduling an agent shows its next run and survives a reload', async ({ page }) => {
  await page.goto('/#agents')
  const agent = page.getByRole('listitem').filter({ hasText: 'Profit Agent' })

  await agent.getByLabel('Schedule').selectOption({ label: 'Daily at 09:00 UTC' })
  await expect(page.getByText(/Profit Agent: Daily at 09:00 UTC/)).toBeVisible()
  await expect(agent).toContainText('Next run')

  await page.reload()
  const reloaded = page.getByRole('listitem').filter({ hasText: 'Profit Agent' })
  await expect(reloaded.getByLabel('Schedule')).toHaveValue('0 9 * * *')

  await reloaded.getByLabel('Schedule').selectOption({ label: 'Not scheduled' })
  await expect(reloaded).toContainText('Runs only when started by hand')
})

test('giving a dish a recipe replaces its estimated cost', async ({ page }) => {
  await page.goto('/#menu')
  const dish = page.getByRole('row').filter({ hasText: 'Caesar Salad' })
  await expect(dish).toContainText('estimated')

  await dish.getByRole('button', { name: 'Recipe' }).click()
  const inventory = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  const lettuce = inventory.find((i: { name: string }) => i.name === 'Romaine Lettuce')
  await page.getByRole('button', { name: 'Ingredient', exact: true }).click()
  await page.getByLabel('Ingredient', { exact: true }).selectOption(lettuce.id)
  await page.getByLabel('Quantity', { exact: true }).fill('1')
  await page.getByRole('button', { name: 'Save recipe' }).click()

  await expect(page.getByText('Recipe saved')).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: 'Caesar Salad' })).toContainText('from recipe')
})

test('adding a person and a shift, then removing them', async ({ page }) => {
  await page.goto('/#staff')

  await page.getByRole('button', { name: 'Add person' }).click()
  await page.getByLabel('Name', { exact: true }).fill('Priya Test')
  await page.getByLabel('Role', { exact: true }).fill('Server')
  await page.getByRole('button', { name: 'Save person' }).click()
  const person = page.getByRole('listitem').filter({ hasText: 'Priya Test' })
  await expect(person).toContainText('No shifts')

  await page.getByRole('button', { name: 'Add shift' }).click()
  await page.getByLabel('Who').selectOption({ label: 'Priya Test — Server' })
  await page.getByRole('button', { name: 'Schedule shift' }).click()
  await expect(person).toContainText('1 upcoming')

  const shiftRow = page
    .locator('div.row', { hasText: 'Priya Test' })
    .filter({ has: page.getByRole('button', { name: 'Remove shift' }) })
  await shiftRow.getByRole('button', { name: 'Remove shift' }).click()
  await expect(person).toContainText('No shifts')
  page.once('dialog', (d) => d.accept())
  await person.getByRole('button', { name: 'Remove Priya Test' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: 'Priya Test' })).toHaveCount(0)
})
