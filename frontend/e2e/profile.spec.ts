import { type Page, expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

// Seeded by backend/scripts/seed_e2e.py: an owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

async function signIn(page: Page, credentials: { email: string; password: string }) {
  const response = await page.request.post(`${API}/api/v1/auth/login`, { data: credentials })
  expect(response.ok()).toBeTruthy()
}

test('an owner edits the restaurant details, with mistakes caught first, and the change sticks', async ({ page }) => {
  await signIn(page, OWNER)
  await page.goto('/#profile')
  const form = page.getByRole('form', { name: 'Restaurant details' })
  const name = form.getByLabel('Restaurant name')

  // Loaded from the server: whatever the restaurant is called right now.
  await expect(name).not.toHaveValue('')
  const save = form.getByRole('button', { name: 'Save changes' })
  await expect(save).toBeDisabled() // nothing changed yet

  const newName = `Harborview ${Date.now()}`
  await name.fill(newName)
  await form.getByLabel('Contact email').fill('not-an-email')
  await form.getByLabel('Tuesday opens').fill('09:00')
  await form.getByLabel('Tuesday closes').fill('09:00')
  await save.click()

  // Refused in the browser, next to each field, before anything is sent.
  await expect(form.getByRole('alert')).toHaveText('2 entries need fixing before you can save.')
  await expect(form.getByText('Enter a valid email address, or leave it blank.')).toBeVisible()
  await expect(form.getByText('Opening and closing times can’t be the same.')).toBeVisible()
  await expect(form.getByLabel('Contact email')).toBeFocused()

  await form.getByLabel('Contact email').fill('hello@harborview.example.com')
  await form.getByLabel('Tuesday closes').fill('22:30')
  await expect(form.getByRole('alert')).toHaveCount(0)
  await save.click()

  await expect(page.getByText('Restaurant details saved')).toBeVisible()
  await expect(save).toBeDisabled()
  await expect(form.getByText(/Last updated .* by E2E Manager/)).toBeVisible()
  // The sidebar and the dashboard use the new values straight away.
  await expect(page.locator('.brand-name')).toHaveText(newName)

  // After a refresh the saved values come back from the server.
  await page.reload()
  await expect(name).toHaveValue(newName)
  await expect(form.getByLabel('Contact email')).toHaveValue('hello@harborview.example.com')
  await expect(form.getByLabel('Tuesday closes')).toHaveValue('22:30')

  await page.goto('/#overview')
  await expect(page.locator('.page-subtitle').first()).toContainText(newName)
})

test('staff, menu, inventory and scheduling are linked, not edited, from the details tab', async ({ page }) => {
  await signIn(page, OWNER)
  await page.goto('/#profile/details')
  const panel = page.getByRole('tabpanel')
  await expect(panel.getByRole('form', { name: 'Restaurant details' })).toBeVisible()
  await expect(panel.getByLabel('Staff roles')).toHaveCount(0)
  await expect(panel.getByLabel('Longest shift (hours)')).toHaveCount(0)

  await panel.getByRole('link', { name: 'Staff & scheduling' }).click()
  await expect(page.getByRole('heading', { name: 'Schedule & staff', level: 1 })).toBeVisible()
})

test('a member sees the details but cannot change them', async ({ page }) => {
  // Signing up once the restaurant exists makes a member, not an owner.
  const email = `member-${Date.now()}@example.com`
  const response = await page.request.post(`${API}/api/v1/auth/signup`, {
    data: { name: 'Mia Member', email, password: 'a-long-password' },
  })
  expect(response.ok()).toBeTruthy()
  expect((await response.json()).role).toBe('member')

  await page.goto('/#profile')
  await expect(page.getByText('Only the owner or an admin can change them.')).toBeVisible()
  const form = page.getByRole('form', { name: 'Restaurant details' })
  await expect(form.getByLabel('Restaurant name')).not.toHaveValue('')
  await expect(form.getByLabel('Restaurant name')).toBeDisabled()
  await expect(form.getByRole('button', { name: 'Save changes' })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'Team access' })).toHaveCount(0)
})
