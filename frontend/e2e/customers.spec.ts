import { expect, test } from '@playwright/test'
import { joinAsMember } from './team'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

test('a request is logged, summarised into action items by the agent, and kept in its original words', async ({ page }) => {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  const customer = `Nadia Test ${Date.now()}`
  const words = 'Our starters took 50 minutes.\n\nNobody apologised — we won’t be back unless that changes.'

  await page.goto('/#customers')
  await expect(page.getByRole('heading', { name: 'Customer requests', level: 1 })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Summarise open requests' })).toBeDisabled() // nothing to read yet

  // --- write it down
  await page.getByRole('button', { name: 'Log a request' }).click()
  const form = page.getByRole('form', { name: 'Log a customer request' })
  await form.getByLabel('What the customer said').fill(words)
  await form.getByLabel('Type').selectOption('complaint')
  await form.getByLabel('How it reached you').selectOption('phone')
  await form.getByLabel('Customer’s name (optional)').fill(customer)
  await form.getByLabel('Phone or email (optional)').fill('555-0100')
  await form.getByRole('button', { name: 'Save request' }).click()

  const request = page.getByRole('list', { name: 'Customer requests' }).getByRole('listitem').filter({ hasText: customer })
  await expect(request).toContainText('Complaint')
  await expect(request).toContainText('Contact: 555-0100')
  // Word for word, line breaks included.
  expect(await request.getByRole('blockquote').innerText()).toBe(words)

  // --- the Assistant Manager reads the open requests
  await page.getByRole('button', { name: 'Summarise open requests' }).click()
  const review = page.locator('section.card', { has: page.getByRole('heading', { name: 'Summary and action items' }) })
  await expect(review).toContainText('1 open customer request: 1 complaint.')
  const item = review.getByRole('listitem').filter({ hasText: 'Follow up the complaint' })
  await expect(item).toContainText('High priority')
  await expect(item).toContainText('Our starters took 50 minutes.')

  // The action item leads back to the request it came from, and can be ticked off.
  await item.getByRole('button', { name: new RegExp(customer) }).click()
  await expect(request).toHaveClass(/request-highlight/)
  await item.getByRole('checkbox').check()
  await expect(item).toContainText(`Done by ${OWNER.email}`)

  // --- resolving adds a note beside the original
  await request.getByRole('button', { name: 'Resolve' }).click()
  await request.getByLabel('What was done about it (optional)').fill('Called her, apologised, sent a voucher.')
  await request.getByRole('button', { name: 'Mark resolved' }).click()
  await expect(request).toHaveCount(0) // no longer open

  await page.reload()
  await expect(review.getByRole('listitem').filter({ hasText: 'Follow up the complaint' }).getByRole('checkbox')).toBeChecked()
  await page.getByRole('button', { name: /^Resolved/ }).click()
  await expect(request).toContainText('Called her, apologised, sent a voucher.')
  expect(await request.getByRole('blockquote').innerText()).toBe(words)
})

test('staff can write a request down but not read the others', async ({ page }) => {
  await joinAsMember(page.request, { name: 'Sam Server', email: `sam-${Date.now()}@example.com` })

  await page.goto('/#customers')
  await page.getByLabel('What the customer said').fill('Table 4 asked whether we do gluten-free pasta.')
  await page.getByRole('button', { name: 'Save request' }).click()
  await expect(page.getByText('Request saved')).toBeVisible()

  await expect(page.getByRole('heading', { name: 'Requests', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Summarise open requests' })).toHaveCount(0)
  expect((await page.request.get(`${API}/api/v1/customer-requests`)).status()).toBe(403)
})
