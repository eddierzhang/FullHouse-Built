import { expect, test } from '@playwright/test'
import { joinAsMember } from './team'

test('staff see the restaurant but not the controls that are for owners and admins', async ({ page }) => {
  await joinAsMember(page.request, { name: 'Sky Staff', email: `sky-${Date.now()}@example.com` })

  await page.goto('/#overview')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('button', { name: 'New task' })).toHaveCount(0)
  await expect(page.getByText('Revenue, last 7 days')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: /forecast/i })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Profit & forecast' })).toHaveCount(0)

  // A typed-in link to the profit page lands on the overview instead.
  await page.goto('/#profit')
  await expect(page.getByText('Revenue, last 7 days')).toHaveCount(0)

  // The seeded proposals are visible, but deciding them is for managers.
  await page.goto('/#approvals')
  await expect(page.getByRole('heading', { level: 1, name: 'Recommendations' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Details' }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: 'Approve' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Reject' })).toHaveCount(0)

  await page.goto('/#menu')
  await expect(page.getByRole('heading', { level: 1, name: 'Menu' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Add dish' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Price' })).toHaveCount(0)

  await page.goto('/#suppliers')
  await expect(page.getByRole('heading', { level: 1, name: 'Suppliers' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Add supplier' })).toHaveCount(0)

  await page.goto('/#agents')
  await expect(page.getByRole('button', { name: 'Run now' })).toHaveCount(0)
})
