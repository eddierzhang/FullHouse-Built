import { expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
// Seeded by backend/scripts/seed_e2e.py: the owner of an already set-up restaurant.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

test('help, feedback from anywhere, and downloading the data', async ({ page }) => {
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
  const note = `The count screen confused me ${Date.now()}`

  // Feedback from the sidebar, on whatever page you're on.
  await page.goto('/#inventory')
  await page.getByRole('button', { name: 'Send feedback' }).first().click()
  const dialog = page.getByRole('dialog', { name: 'Send feedback' })
  await dialog.getByLabel('What happened?').fill(note)
  await dialog.getByRole('button', { name: 'Send' }).click()
  await expect(page.getByText('Thanks, feedback sent')).toBeVisible()

  // The Help page: a guide, and what the team has sent.
  await page.goto('/#help')
  await expect(page.getByRole('heading', { level: 1, name: 'Help' })).toBeVisible()
  await page.getByText('Log a delivery').click()
  await expect(page.getByText('it won’t be counted twice')).toBeVisible()
  const sent = page.getByRole('list', { name: 'Feedback' }).getByRole('listitem').filter({ hasText: note })
  await expect(sent).toContainText('on inventory')

  // Everything, as a zip.
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download all data (.zip)' }).click()
  expect((await download).suggestedFilename()).toMatch(/^fullhouse-export-\d{4}-\d{2}-\d{2}\.zip$/)
  await expect(page.getByText('Export downloaded')).toBeVisible()
})
