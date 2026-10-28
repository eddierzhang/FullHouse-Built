import path from 'node:path'
import { expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
// Seeded by backend/scripts/seed_e2e.py: eight weeks of invoices from four suppliers, a digest, open questions.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

const ASK = 'Ask about prices, invoices, or suppliers'

test.beforeEach(async ({ page }) => {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
})

test('the digest opens on the dollars at stake, in three fixed sections, and "Not useful" hides a card', async ({ page }) => {
  await page.goto('/#digest')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^\$[\d,]+\.\d\d$/)
  await expect(page.getByText(/at stake this week, across \d+ items?, \d+ suppliers?/)).toBeVisible()

  const headings = page.getByRole('heading', { level: 2 })
  await expect(headings.nth(0)).toContainText('Switch supplier')
  await expect(headings.nth(1)).toContainText('Price went up')
  await expect(headings.nth(2)).toContainText('Owed to you')

  // An item can sit in two sections (cheaper elsewhere, and up at its supplier); hiding it clears both.
  const section = page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Switch supplier' }) })
  const name = (await section.getByRole('article').first().getAttribute('aria-label'))!
  const card = section.getByRole('article', { name, exact: true })
  await card.getByRole('button', { name: 'Not useful' }).click()
  await expect(card).toHaveCount(0)
  const hidden = section.getByRole('status').filter({ hasText: `${name} hidden` })
  await expect(hidden).toBeVisible()
  await hidden.getByRole('button', { name: 'Undo' }).click()
  await expect(card).toBeVisible()
})

test('the questions page is a count and the cards; answering one removes it', async ({ page }) => {
  await page.goto('/#questions')
  const heading = page.getByRole('heading', { level: 1 })
  await expect(heading).toHaveText(/^\d+ questions?$/)
  const before = Number((await heading.innerText()).split(' ')[0])
  expect(before).toBeGreaterThanOrEqual(1)

  const first = page.getByRole('article').first()
  await expect(first).toContainText(/Is this/)
  await expect(first).toContainText(/on this invoice/)
  const option = first.getByRole('button').first()
  await option.click()
  await expect(heading).toHaveText(`${before - 1} ${before - 1 === 1 ? 'question' : 'questions'}`)
})

test('a question typed into the field is answered in the conversation, with its sources', async ({ page }) => {
  await page.goto('/#digest')
  const field = page.getByPlaceholder(ASK).first()
  await field.fill('what did I pay for tomatoes')
  await field.press('Enter')

  await expect(page).toHaveURL(/#thread$/)
  const log = page.getByRole('log')
  await expect(log).toContainText('what did I pay for tomatoes')
  const answer = log.locator('.pi-agent').last()
  await expect(answer.getByRole('link')).not.toHaveCount(0, { timeout: 20_000 })
  await expect(answer).not.toContainText('Reading the invoices')
  // No "I think": facts in the past tense, with a dollar figure.
  await expect(answer).toContainText(/\$\d/)
})

test('"Draft message" on a price rise opens the editor; Discard returns to the digest', async ({ page }) => {
  await page.goto('/#digest')
  const section = page.locator('section', { has: page.getByRole('heading', { level: 2, name: 'Price went up' }) })
  await section.getByRole('button', { name: 'Draft message' }).first().click()

  await expect(page).toHaveURL(/#draft\//)
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Message to')
  await expect(page.getByLabel('Subject')).not.toHaveValue('')
  await expect(page.getByLabel('Body')).not.toHaveValue('')
  await expect(page.getByText('Attached invoice lines')).toBeVisible()
  for (const name of ['Send via email', 'Copy', 'Discard']) {
    await expect(page.getByRole('button', { name })).toBeVisible()
  }

  // Sending always goes through a confirm sheet that names the recipient.
  await page.getByLabel('Rep’s email').fill('rep@supplier.example')
  await page.getByRole('button', { name: 'Send via email' }).click()
  const sheet = page.getByRole('dialog', { name: 'Send this email?' })
  await expect(sheet).toContainText('rep@supplier.example')
  await sheet.getByRole('button', { name: 'Cancel' }).click()
  await expect(sheet).toHaveCount(0)

  await page.getByRole('button', { name: 'Discard' }).click()
  await expect(page.getByText('Draft discarded')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^\$[\d,]+\.\d\d$/)
})

test('the price pages sit in the sidebar beside the dashboards, each with its own controls', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: /^Good/ })).toBeVisible()
  const group = page.getByRole('navigation', { name: 'Invoices & prices' })
  // In the order an invoice moves through: read, asked about, kept by item, reported on, talked over.
  await expect(group.getByRole('link')).toHaveText([/Invoices/, /Questions/, /Items/, /Digest/, /Conversation/])
  // The dashboards keep the Assistant Manager's controls.
  await expect(page.getByRole('button', { name: 'New task' })).toBeVisible()
  await expect(page.getByPlaceholder('Ask about prices, invoices, or suppliers')).toHaveCount(0)
  // The price pages have the agent's field where New task was.
  await group.getByRole('link', { name: /Digest/ }).click()
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^\$[\d,]+\.\d\d$/)
  await expect(page.getByRole('button', { name: 'New task' })).toHaveCount(0)
  await expect(page.getByPlaceholder('Ask about prices, invoices, or suppliers').first()).toBeVisible()
})

test('a PDF is read into lines before anything is saved', async ({ page }) => {
  await page.goto('/#invoices')
  await page.getByRole('button', { name: 'Add invoice' }).click()
  await page.getByRole('button', { name: /Forward invoices/ }).click()
  await page.getByLabel('Invoice PDF').setInputFiles(path.resolve('../backend/fixtures/invoices/worldwide-produce-2026-10-21.pdf'))

  const form = page.getByRole('form', { name: 'Invoice lines' })
  await expect(form).toBeVisible({ timeout: 20_000 })
  // The rows are inputs, so the text is in their accessible names, not their text content.
  await expect(form.getByRole('row', { name: /KALE GREEN 24 CT/ })).toBeVisible()
  // The header was read too: supplier, date and the printed total, for the person to check.
  const supplier = form.getByRole('combobox', { name: 'Supplier', exact: true })
  const chosen = await supplier.evaluate((el) => (el as HTMLSelectElement).selectedOptions[0]?.textContent ?? '')
  if (chosen === 'Another supplier') await expect(form.getByLabel('Supplier name')).toHaveValue(/Worldwide Produce/)
  else expect(chosen).toContain('Worldwide Produce')
  await expect(form.getByLabel('Invoice date')).toHaveValue('2026-10-21')
  await expect(form.getByLabel('Invoice total')).not.toHaveValue('')
  // Nothing was saved: the list is unchanged.
  await form.getByRole('button', { name: 'Cancel' }).click()
})

test('the queue answers from the keyboard', async ({ page }) => {
  await page.goto('/#questions')
  const heading = page.getByRole('heading', { level: 1 })
  await expect(heading).toHaveText(/^\d+ questions?$/)
  const before = Number((await heading.innerText()).split(' ')[0])
  expect(before).toBeGreaterThanOrEqual(2)

  const firstText = await page.getByRole('article').first().getAttribute('aria-label')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('article').first()).not.toHaveAttribute('aria-label', firstText!)
  await expect(heading).toHaveText(`${before} questions`)

  await page.keyboard.press('Enter')
  await expect(heading).toHaveText(`${before - 1} ${before - 1 === 1 ? 'question' : 'questions'}`)
})

test('a manager can email the digest to themselves', async ({ page }) => {
  await page.goto('/#digest')
  await page.getByRole('button', { name: 'Email me this digest' }).click()
  await expect(page.getByRole('status').filter({ hasText: /Sent to|printed to the server log/ })).toBeVisible()
})

test('the inventory page shows what the invoices paid, and the item page says what it is counted as', async ({ page }) => {
  await page.goto('/#inventory')
  await expect(page.getByRole('note', { name: 'Stock and the invoices' })).toContainText('priced from the invoices')
  const row = page.getByRole('row').filter({ hasText: 'Tomatoes' }).first()
  const paid = row.getByRole('link', { name: /^Last paid \$\d+\.\d\d\/lb$/ })
  await expect(paid).toBeVisible()
  await paid.click()
  await expect(page.getByRole('heading', { level: 1, name: 'Tomatoes, 25 lb case' })).toBeVisible()
  await expect(page.getByText(/^Counted in Inventory as Tomatoes: \d+(\.\d)? lb on hand/)).toBeVisible()
})

test('Items says what its numbers mean: last price per pack, the eight-week change with its share, and a summary line', async ({ page }) => {
  await page.goto('/#items')
  await expect(page.getByRole('heading', { level: 1, name: 'Items' })).toBeVisible()
  await expect(page.getByText(/The figure on the right is the change per pack/)).toBeVisible()
  await expect(page.getByLabel('Items summary')).toContainText(/^\d+ items? matched from your invoices\./)
  const rows = page.getByRole('listitem')
  await expect(rows.first()).toContainText(/Last paid \$[\d,]+\.\d\d a \w+ at /)
  await expect(rows.first()).toContainText(/a \w+, (up|down) \d+% from \$[\d,]+\.\d\d on /)
})
