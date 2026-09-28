import { expect, test } from '@playwright/test'

// Runs against its own site (see playwright.config.ts): sample data, but no
// restaurant set up yet -- what a brand-new deployment's first owner sees.

const OWNER = { name: 'Rosa Owner', email: `rosa-${Date.now()}@example.com`, password: 'harbor-house-1' }

test('sign up, set up the restaurant, open the dashboard, and edit settings', async ({ page }) => {
  // --- sign up: a new account is an owner, sent to setup rather than the dashboard
  await page.goto('/')
  await page.getByRole('button', { name: 'Create an account' }).click()
  await page.getByLabel('Name').fill(OWNER.name)
  await page.getByLabel('Email').fill(OWNER.email)
  await page.getByLabel('Password').fill(OWNER.password)
  await page.getByRole('button', { name: 'Create account' }).click()

  await expect(page.getByRole('heading', { name: 'Set up your restaurant' })).toBeVisible()
  await expect(page.getByRole('navigation')).toHaveCount(0)
  await expect(page.getByText('Step 1 of 6')).toBeVisible()

  // --- step 1: details, with a mistake caught first
  await page.getByLabel('Restaurant name').fill('Harbor House')
  await page.getByLabel('Street address').fill('88 Pier Road')
  await page.getByLabel('City').fill('Chicago')
  await page.getByLabel('Country').fill('United States')
  await page.getByRole('button', { name: 'Save and continue' }).click()
  await expect(page.getByRole('alert')).toContainText('Phone')
  await page.getByLabel('Phone').fill('+1 312 555 0199')
  await page.getByRole('button', { name: 'Save and continue' }).click()

  // --- step 2: timezone and hours
  await expect(page.getByText('Step 2 of 6')).toBeVisible()
  await page.getByRole('combobox', { name: /^Timezone/ }).selectOption('America/Chicago')
  await page.locator('.hours-row', { hasText: 'Monday' }).getByRole('checkbox').uncheck()
  await page.getByLabel('Friday closes').fill('23:30')
  await page.getByRole('button', { name: 'Save and continue' }).click()

  // --- step 3: inventory, then leave partway through
  await expect(page.getByText('Step 3 of 6')).toBeVisible()
  await page.getByLabel('Safety stock (days)').fill('3')
  await page.getByRole('button', { name: 'Save and finish later' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()

  // --- come back: setup resumes at the first unsaved step, nothing lost
  await page.getByLabel('Email').fill(OWNER.email)
  await page.getByLabel('Password').fill(OWNER.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText('Step 4 of 6')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Staff roles', level: 2 })).toBeVisible()
  await page.getByRole('button', { name: 'Back' }).click()
  await expect(page.getByLabel('Safety stock (days)')).toHaveValue('3')
  await page.getByRole('button', { name: 'Save and continue' }).click()

  // --- step 4: staff roles
  await page.getByRole('button', { name: 'Remove Host' }).click()
  await page.getByLabel('New role').fill('Sommelier')
  await page.getByRole('button', { name: 'Add role' }).click()
  await page.getByRole('button', { name: 'Save and continue' }).click()

  // --- step 5: scheduling rules
  await expect(page.getByText('Step 5 of 6')).toBeVisible()
  await page.getByLabel('Longest shift (hours)').fill('9')
  await page.getByRole('button', { name: 'Save and continue' }).click()

  // --- review, then finish
  await expect(page.getByRole('heading', { name: 'Review and finish' })).toBeVisible()
  const review = page.locator('.review')
  await expect(review.locator('.review-row', { hasText: 'Name' }).first()).toContainText('Harbor House')
  await expect(review.locator('.review-row', { hasText: 'Monday' })).toContainText('Closed')
  await expect(review.locator('.review-row', { hasText: 'Friday' })).toContainText('23:30')
  await expect(review.locator('.review-row', { hasText: 'Roles' })).toContainText('Sommelier')
  await expect(review.locator('.review-row', { hasText: 'Longest shift' })).toContainText('9 hours')
  await page.getByRole('button', { name: 'Finish setup' }).click()

  // --- the dashboard, initialised from setup
  await expect(page.getByRole('navigation').first()).toBeVisible()
  await expect(page.locator('.brand-name')).toHaveText('Harbor House')
  await expect(page.locator('.page-subtitle').first()).toContainText('Harbor House')

  await page.goto('/#agents')
  await expect(page.getByText('Schedules run on America/Chicago time')).toBeVisible()
  // The Assistant Manager reviews an hour before the usual 11:00 opening, on the six open days.
  await expect(page.getByText('Tue, Wed, Thu, Fri, Sat, Sun at 10:00').first()).toBeVisible()

  await page.goto('/#staff')
  await page.getByRole('button', { name: 'Add person' }).click()
  const roles = await page.getByLabel('Role', { exact: true }).locator('option').allInnerTexts()
  expect(roles).toContain('Sommelier')
  expect(roles).not.toContain('Host')

  // --- edit on the Profile page, and the change sticks (the old #settings link still lands there)
  await page.goto('/#settings')
  await expect(page.getByRole('heading', { name: 'Profile', level: 1 })).toBeVisible()
  const details = page.getByRole('form', { name: 'Restaurant details' })
  await expect(details.getByLabel('Restaurant name')).toHaveValue('Harbor House')
  await details.getByLabel('Restaurant name').fill('Harbor House Kitchen')
  await details.getByLabel('Phone').fill('+1 312 555 0123')
  await details.getByRole('button', { name: 'Save changes' }).click()
  await expect(page.getByText('Restaurant details saved')).toBeVisible()
  await expect(page.locator('.brand-name')).toHaveText('Harbor House Kitchen')

  await page.getByRole('tab', { name: 'Operating rules' }).click()
  const scheduling = page.getByRole('form', { name: 'Scheduling rules' })
  await scheduling.getByLabel('Weekly hours per person (max)').fill('36')
  await scheduling.getByRole('button', { name: 'Save changes' }).click()
  await expect(scheduling.getByRole('button', { name: 'Save changes' })).toBeDisabled()

  await page.reload()
  await expect(page.getByRole('form', { name: 'Scheduling rules' }).getByLabel('Weekly hours per person (max)')).toHaveValue('36')
  await page.goto('/#profile/details')
  await expect(page.locator('.brand-name')).toHaveText('Harbor House Kitchen')
  await expect(page.getByRole('form', { name: 'Restaurant details' }).getByLabel('Phone')).toHaveValue('+1 312 555 0123')

  // Signing out and back in goes straight to the dashboard now.
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await page.getByLabel('Email').fill(OWNER.email)
  await page.getByLabel('Password').fill(OWNER.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.locator('.brand-name')).toHaveText('Harbor House Kitchen')
})
