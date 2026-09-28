import { expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

test('an invited teammate joins, gets a password reset, and loses access the moment it is turned off', async ({ page, browser }) => {
  const email = `rio-${Date.now()}@example.com`

  // --- the owner creates an invite link for this email
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  await page.goto('/#profile/team')
  await page.getByLabel('Their email (optional)').fill(email)
  await page.getByRole('button', { name: 'Create invite link' }).click()
  const inviteUrl = await page.getByRole('textbox', { name: 'Invite link' }).inputValue()
  expect(inviteUrl).toContain('#invite/')
  await page.getByRole('button', { name: 'Done' }).click()
  await expect(page.getByRole('list', { name: 'Unused invites' })).toContainText(email)

  // --- the teammate opens it in their own browser and joins
  const teammateContext = await browser.newContext()
  const teammate = await teammateContext.newPage()
  await teammate.goto(inviteUrl)
  await expect(teammate.getByRole('heading', { name: 'Join your team' })).toBeVisible()
  await expect(teammate.getByLabel('Email')).toHaveValue(email)
  await teammate.getByLabel('Name').fill('Rio Runner')
  await teammate.getByLabel('Password').fill('a-long-password')
  await teammate.getByRole('button', { name: 'Create account' }).click()
  await expect(teammate.getByText('Rio Runner')).toBeVisible() // in the app, signed in

  // The link was single-use.
  const again = await browser.newPage()
  await again.goto(inviteUrl)
  await expect(again.getByText(/expired or was already used/)).toBeVisible()
  await again.close()

  // --- the owner issues a reset link, which the teammate uses
  await page.reload()
  const row = page.getByRole('row').filter({ hasText: email })
  await row.getByRole('button', { name: 'Reset password' }).click()
  const resetUrl = await page.getByRole('textbox', { name: /Password reset link/ }).inputValue()
  await page.getByRole('button', { name: 'Done' }).click()

  await teammate.goto(resetUrl)
  await expect(teammate.getByRole('heading', { name: 'Set a new password' })).toBeVisible()
  await teammate.getByLabel('New password').fill('another-long-one')
  await teammate.getByRole('button', { name: 'Save password' }).click()
  await expect(teammate.getByText('Rio Runner')).toBeVisible()

  // --- turning access off signs them out on their very next request
  page.once('dialog', (d) => d.accept())
  await row.getByRole('button', { name: 'Turn access off' }).click()
  await expect(row).toContainText('Access off')
  await teammate.goto('/#inventory')
  await expect(teammate.getByRole('heading', { name: 'Sign in' })).toBeVisible()

  // --- and the security log shows what happened
  await page.goto('/#profile/security')
  const log = page.getByRole('table', { name: 'Security events' })
  await expect(log).toContainText('Joined with an invite')
  await expect(log).toContainText('Password reset with a link')
  await expect(log).toContainText('Access turned off')

  await teammateContext.close()
})
