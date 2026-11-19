import { expect, test } from '@playwright/test'

/** A restaurant's own CSV, chosen in a section, is read, checked and entered without anything being retyped. */
const file = (name: string, text: string) => ({ name, mimeType: 'text/csv', buffer: Buffer.from(text) })
const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('fullhouse.tour.seen', '1'))
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
})

test('a suppliers file is recognised and entered; a second time changes nothing; a bad row enters nothing', async ({ page }) => {
  const stamp = Date.now()
  await page.goto('/#suppliers')
  const card = page.locator('details.csv-upload')
  await card.locator('summary').click()
  await card.getByLabel('Spreadsheet (CSV)').setInputFiles(file('suppliers.csv', `Supplier,Contact,Lead time days\nUpload Farm ${stamp},orders@upload.example,2\n`))
  await expect(card.getByRole('status').filter({ hasText: 'Read as suppliers' })).toContainText('1 entered, 0 updated, 0 already here')
  await expect(page.getByRole('main').getByText(`Upload Farm ${stamp}`).first()).toBeVisible()

  await card.getByLabel('Spreadsheet (CSV)').setInputFiles(file('suppliers.csv', `Supplier,Contact,Lead time days\nUpload Farm ${stamp},orders@upload.example,2\n`))
  await expect(card.getByRole('status').filter({ hasText: 'Read as suppliers' })).toContainText('0 entered, 0 updated, 1 already here')

  await card.getByLabel('Spreadsheet (CSV)').setInputFiles(file('dishes.csv', `Dish,Category,Price\nUpload Soup ${stamp},Starter,free\n`))
  await expect(card.getByRole('status').filter({ hasText: 'Nothing was entered' })).toContainText('1 row needs fixing in this file of dishes')
  await expect(card.getByRole('cell', { name: /isn't a number/ })).toBeVisible()
})

test('a file whose columns do not say what it is waits for a person to say', async ({ page }) => {
  const stamp = Date.now()
  await page.goto('/#menu')
  const card = page.locator('details.csv-upload')
  await card.locator('summary').click()
  await card.getByLabel('Spreadsheet (CSV)').setInputFiles(file('list.csv', `Name,Price\nUpload Chowder ${stamp},7.50\n`))
  await expect(card.getByRole('status').filter({ hasText: 'Nothing was entered' })).toContainText('do not say clearly what this file is')
  await card.getByLabel('What the file holds').selectOption('dishes')
  await card.getByRole('button', { name: 'Enter the rows' }).click()
  await expect(card.getByRole('status').filter({ hasText: 'Read as dishes' })).toContainText('1 entered')
  await expect(page.getByRole('main').getByText(`Upload Chowder ${stamp}`).first()).toBeVisible()
})
