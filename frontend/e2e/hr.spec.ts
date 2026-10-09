import { expect, test } from '@playwright/test'
import { joinAsMember } from './team'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

test('an employee sees only their published shifts and edits only their own contact details', async ({ page, browser }) => {
  const stamp = Date.now()
  const name = `Dana Server ${stamp}`
  const email = `dana-${stamp}@example.com`
  const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10)

  // --- the owner puts the employee's email on a profile and drafts a shift for them
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  await page.goto('/#staff')
  await page.getByRole('button', { name: 'Add person' }).click()
  await page.getByLabel('Name', { exact: true }).fill(name)
  await page.getByLabel('Role', { exact: true }).selectOption('Server')
  await page.getByLabel('Email (optional)').fill(email)
  await page.getByRole('button', { name: 'Save person' }).click()
  const person = page.getByRole('listitem').filter({ hasText: name })
  await expect(person).toContainText('No login yet')

  await page.getByRole('button', { name: 'Add shift' }).click()
  await page.getByLabel('Who').selectOption({ label: `${name} — Server` })
  await page.getByLabel('Date').fill(tomorrow)
  await page.getByRole('button', { name: 'Add draft shift' }).click()
  await expect(person).toContainText('1 upcoming')

  // --- the employee joins with that email, in their own browser
  const employeeContext = await browser.newContext()
  const employee = await employeeContext.newPage()
  await joinAsMember(employee.request, { name: 'Dana', email })
  await employee.goto('/#staff')
  const mine = employee.locator('section.card', { has: employee.getByRole('heading', { name: 'My shifts' }) })
  await expect(mine).toContainText('No upcoming shifts.') // still a draft
  await expect(employee.getByRole('button', { name: 'Add person' })).toHaveCount(0)
  await expect(employee.getByRole('button', { name: 'Add shift' })).toHaveCount(0)
  await expect(employee.getByRole('button', { name: `Edit ${name}` })).toHaveCount(0)

  // --- once the owner publishes, the employee sees it
  await page.reload()
  await expect(person).not.toContainText('No login yet')
  await page.getByRole('button', { name: /^Publish \d+ shifts?$/ }).click()
  await expect(page.getByText(/^Schedule published/)).toBeVisible()
  await employee.reload()
  await expect(mine).toContainText('1 upcoming')

  // --- and keeps their own contact details up to date
  const details = employee.locator('section.card', { has: employee.getByRole('heading', { name: 'My details' }) })
  await details.getByRole('button', { name: 'Edit' }).click()
  await details.getByLabel('Phone').fill('555-0142')
  await details.getByRole('button', { name: 'Save' }).click()
  await expect(employee.getByText('Your details are saved')).toBeVisible()
  await expect(details).toContainText('555-0142')

  await employeeContext.close()
})
