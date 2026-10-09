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

// Seeded by backend/scripts/seed_e2e.py.
const E2E_EMAIL = 'e2e@example.com'
const E2E_PASSWORD = 'e2e-password'

test.describe('signing in', () => {
  test('the dashboard stays behind the login page until you sign in, and again after signing out', async ({ page }) => {
    const errors = watchForErrors(page)
    await page.goto('/#inventory')
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    await expect(page.getByRole('navigation')).toHaveCount(0)

    await page.getByLabel('Email').fill(E2E_EMAIL)
    await page.getByLabel('Password').fill('not-the-password')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toHaveText("That email and password don't match.")

    await page.getByLabel('Password').fill(E2E_PASSWORD)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('heading', { name: 'Inventory', level: 1 })).toBeVisible()

    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    // The expected 401s from checking for a session aren't page errors.
    expect(errors.filter((e) => !e.includes('401'))).toEqual([])
  })

  test('the landing page explains FullHouse and leads to signing in', async ({ page }) => {
    const errors = watchForErrors(page)
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1 })).toContainText('assistant manager')
    for (const section of ['How it works', 'The whole back office, in one place', 'Your restaurant stays yours']) {
      await expect(page.getByRole('heading', { name: section })).toBeVisible()
    }
    await expect(page.getByLabel('Email')).toHaveCount(0)

    await page.getByRole('navigation', { name: 'Account' }).getByRole('link', { name: 'Sign in' }).click()
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
    await page.getByLabel('Email').fill(E2E_EMAIL)
    await page.getByLabel('Password').fill(E2E_PASSWORD)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('navigation').getByRole('link', { name: /^Help/ })).toBeVisible()
    await expect(page).toHaveURL(/\/$/) // the address that led to the form isn't kept

    // The brand on the sign-in form leads back to the landing page.
    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.getByRole('link', { name: 'FullHouse home' }).click()
    await expect(page.getByRole('heading', { name: 'How it works' })).toBeVisible()
    expect(errors.filter((e) => !e.includes('401'))).toEqual([])
  })

  test('a new restaurant creates its own account, apart from every other', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: 'Set up your restaurant' }).first().click()
    await expect(page.getByRole('heading', { name: 'Create your restaurant’s account' })).toBeVisible()
    await page.getByLabel('Restaurant name').fill('Corner Cafe')
    await page.getByLabel('Your name').fill('New Owner')
    await page.getByLabel('Email').fill(`new-${Date.now()}@example.com`)
    await page.getByLabel('Password').fill('a-long-password')
    await page.getByRole('button', { name: 'Create account' }).click()

    // Its owner, sent to set it up, with the name already filled in.
    await expect(page.getByRole('heading', { name: 'Set up your restaurant' })).toBeVisible()
    await expect(page.getByLabel('Restaurant name')).toHaveValue('Corner Cafe')
    // None of the seeded restaurant's records are theirs to see.
    const items = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
    expect(items).toEqual([])
  })
})

test.describe('signed in', () => {
  // Signs the whole browser context in: page.request shares its cookies.
  test.beforeEach(async ({ page }) => {
    const response = await page.request.post(`${API}/api/v1/auth/login`, {
      data: { email: E2E_EMAIL, password: E2E_PASSWORD },
    })
    expect(response.ok()).toBeTruthy()
  })

async function stockOf(page: Page, item: string): Promise<number> {
  const items = await (await page.request.get(`${API}/api/v1/restaurant/inventory-items`)).json()
  return items.find((i: { name: string }) => i.name === item).quantity_on_hand
}

test.describe('navigation', () => {
  test('every page loads without errors, in the documented order', async ({ page }) => {
    const errors = watchForErrors(page)
    await page.goto('/')

    const nav = page.getByRole('navigation')
    // allInnerTexts doesn't wait: read the links only once the whole sidebar is there.
    await expect(nav.getByRole('link', { name: /^Help/ })).toBeVisible()
    const labels = await nav.getByRole('link').allInnerTexts()
    const names = labels.map((l) => l.replace(/\d+$/, '').trim())
    // The day's work first, then the kitchen, the agent, the records and the restaurant.
    expect(names).toEqual([
      'Today',
      'Inventory',
      'Schedule & staff',
      'Recommendations',
      'Suppliers',
      'Menu',
      'Assistant Manager',
      'Agent activity',
      'Profit & forecast',
      'History & undo',
      'Profile',
      'Help',
    ])

    const headings: Record<string, string> = {
      Inventory: 'Inventory',
      'Schedule & staff': 'Schedule & staff',
      Recommendations: 'Recommendations',
      Suppliers: 'Suppliers',
      Menu: 'Menu',
      'Assistant Manager': 'Assistant Manager',
      'Agent activity': 'Agent activity',
      'Profit & forecast': 'Profit & forecast',
      'History & undo': 'History & undo',
      Profile: 'Profile',
      Help: 'Help',
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
    await expect(page.getByRole('heading', { level: 1, name: 'Schedule & staff' })).toBeVisible()
    await page.goto('/#recommendations')
    await expect(page.getByRole('heading', { level: 1, name: 'Recommendations' })).toBeVisible()
    await page.goto('/#settings')
    await expect(page.getByRole('heading', { level: 1, name: 'Profile' })).toBeVisible()
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

  // One agent, so there's no agent to choose.
  const dialog = page.getByRole('dialog', { name: 'New task for Assistant Manager' })
  await expect(dialog.getByLabel('Agent', { exact: true })).toHaveCount(0)
  await dialog.getByLabel('What should it do?').fill('Check what is running low')
  await dialog.getByRole('button', { name: 'Start task' }).click()

  await expect(page.getByRole('heading', { level: 1, name: 'Agent activity' })).toBeVisible()
  const log = page.getByTestId('run-log')
  await expect(log).toContainText('tool call')
  await expect(log).toContainText('list_low_stock_items')
  await expect(page.getByText('Summary', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /Assistant Manager/ }).first()).toContainText('succeeded')
})

test('scheduling the agent shows its next run and survives a reload', async ({ page }) => {
  await page.goto('/#agents')
  await expect(page.getByRole('listitem').filter({ has: page.getByLabel('Schedule') })).toHaveCount(1)
  await expect(page.getByText('Also checks stock every day at 08:00')).toBeVisible()
  const agent = page.getByRole('listitem').filter({ hasText: 'Assistant Manager' })

  await agent.getByLabel('Schedule').selectOption({ label: 'Daily at 09:00' })
  await expect(page.getByText(/Assistant Manager: Daily at 09:00/)).toBeVisible()
  await expect(agent).toContainText('Next run')

  await page.reload()
  const reloaded = page.getByRole('listitem').filter({ hasText: 'Assistant Manager' })
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

test('adding a person and a draft shift, publishing it, then removing them', async ({ page }) => {
  await page.goto('/#staff')

  await page.getByRole('button', { name: 'Add person' }).click()
  await page.getByLabel('Name', { exact: true }).fill('Priya Test')
  await page.getByLabel('Role', { exact: true }).selectOption('Server')
  await page.getByRole('button', { name: 'Save person' }).click()
  const person = page.getByRole('listitem').filter({ hasText: 'Priya Test' })
  await expect(person).toContainText('No shifts')

  await page.getByRole('button', { name: 'Add shift' }).click()
  await page.getByLabel('Who').selectOption({ label: 'Priya Test — Server' })
  await page.getByRole('button', { name: 'Add draft shift' }).click()
  await expect(person).toContainText('1 upcoming')

  // A new shift is a draft until the manager publishes it.
  const shiftRow = page
    .locator('div.row', { hasText: 'Priya Test' })
    .filter({ has: page.getByRole('button', { name: 'Remove shift' }) })
  await expect(shiftRow).toContainText('Draft')
  await page.getByRole('button', { name: /^Publish \d+ shifts?$/ }).click()
  await expect(page.getByText(/^Schedule published/)).toBeVisible()
  await expect(shiftRow).not.toContainText('Draft')
  await expect(page.getByRole('button', { name: /^Publish \d+ shifts?$/ })).toHaveCount(0)

  // Removing a shift asks first, like removing a person.
  page.once('dialog', (d) => d.accept())
  await shiftRow.getByRole('button', { name: 'Remove shift' }).click()
  await expect(person).toContainText('No shifts')
  page.once('dialog', (d) => d.accept())
  await person.getByRole('button', { name: 'Remove Priya Test' }).click()
  await expect(page.getByRole('listitem').filter({ hasText: 'Priya Test' })).toHaveCount(0)
})
})
