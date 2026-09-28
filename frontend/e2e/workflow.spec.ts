import { type Page, expect, test } from '@playwright/test'

// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

async function signIn(page: Page) {
  await page.goto('/')
  await page.getByLabel('Email').fill(OWNER.email)
  await page.getByLabel('Password').fill(OWNER.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('navigation').first()).toBeVisible()
}

const row = (page: Page, name: string) => page.getByRole('row').filter({ hasText: name }).first()

test('sign in, use stock, the agent proposes a reorder, a manager approves, and the dashboard updates', async ({ page }) => {
  const item = `Saffron ${Date.now()}`

  // --- sign in
  await signIn(page)

  // --- update inventory: add an item, then record using most of it
  await page.goto('/#inventory')
  await page.getByRole('button', { name: 'Add item' }).click()
  const add = page.getByRole('form', { name: 'Add an item' })
  await add.getByLabel('Name').fill(item)
  await add.getByRole('combobox', { name: /^Unit/ }).selectOption('g')
  await add.getByLabel('On hand now').fill('10')
  await add.getByLabel('Reorder at').fill('5')
  await add.getByLabel('Reorder quantity').fill('20')
  await add.getByRole('button', { name: 'Add item' }).click()
  await expect(page.getByText('Item added')).toBeVisible()

  await page.getByRole('button', { name: `Record a stock change for ${item}` }).click()
  const change = page.getByRole('form', { name: `Stock change for ${item}` })
  await change.getByRole('button', { name: 'Used' }).click()
  await change.getByLabel('Amount used (g)').fill('8')
  await change.getByRole('button', { name: 'Save change' }).click()
  await expect(row(page, item)).toContainText('2 g')
  await expect(row(page, item).getByText('Low')).toBeVisible()

  // --- the agent reads the inventory and proposes a change
  await page.getByRole('button', { name: 'New task' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('What should it do?').fill(`Reorder the ${item}, we're nearly out`)
  await dialog.getByRole('button', { name: 'Start task' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Agent activity' })).toBeVisible()
  await expect(page.getByTestId('run-log')).toContainText('propose_reorder')
  await expect(page.getByText('Summary', { exact: true })).toBeVisible()

  // --- a manager approves it
  await page.goto('/#approvals')
  const proposal = page.getByRole('listitem').filter({ hasText: `Reorder 20 × ${item}` })
  await expect(proposal).toBeVisible()
  await proposal.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByText('Approved')).toBeVisible()
  await expect(proposal).toHaveCount(0)

  // --- the dashboard shows the result
  await page.goto('/#inventory')
  await expect(row(page, item)).toContainText('22 g')
  await expect(row(page, item).getByText('Low')).toHaveCount(0)
  await page.getByRole('button', { name: `History of ${item}` }).click()
  const history = page.getByRole('list', { name: 'Stock changes' })
  await expect(history).toContainText('Reorder approved')
})
