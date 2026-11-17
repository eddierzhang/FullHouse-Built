import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

/** Shared by the Margin & Labor browser tests: their own restaurant, and the checks every module page must pass. */
export const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`
export const ML = `${API}/api/v1/margin-labor`
export const ML_OWNER = { email: 'ml@example.com', password: 'ml-password' }
export const ML_STAFF = { email: 'ml-staff@example.com', password: 'ml-password' }

export async function signIn(page: Page, who = ML_OWNER) {
  await page.addInitScript(() => window.localStorage.setItem('fullhouse.tour.seen', '1'))
  expect((await page.request.post(`${API}/api/v1/auth/login`, { data: who })).ok()).toBeTruthy()
}

/** Spec 10.3 and 12: one header sentence of at most 20 words with a digit, at most 3 tiles, exactly 1 chart above the table. */
export async function expectPageRules(page: Page) {
  const sentence = (await page.getByTestId('header-sentence').innerText()).trim()
  expect(page.getByTestId('header-sentence')).toHaveCount(1)
  expect(sentence.split(/\s+/).length, sentence).toBeLessThanOrEqual(20)
  expect(sentence, sentence).toMatch(/\d/)
  expect(sentence).not.toMatch(/!/)
  expect(await page.getByTestId('stat-tile').count()).toBeLessThanOrEqual(3)
  await expect(page.getByTestId('primary-chart')).toHaveCount(1)
  for (const label of await page.locator('.ml-tile-label').allInnerTexts()) expect(label.trim().split(/\s+/).length, label).toBeLessThanOrEqual(3)
  expect(await page.locator('.ml-table thead th').count()).toBeLessThanOrEqual(7)
}

/** A derived number opens the Why panel with a formula, inputs and a timestamp; Escape closes it. */
export async function expectWhyPanel(page: Page, opener: ReturnType<Page['locator']>) {
  await opener.click()
  const panel = page.getByRole('dialog', { name: 'How this was worked out' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Formula', { exact: true })).toBeVisible()
  await expect(panel.locator('dd').first()).not.toBeEmpty()
  await expect(panel.getByText('Inputs', { exact: true })).toBeVisible()
  expect(await panel.locator('table tr').count()).toBeGreaterThan(0)
  await expect(panel.getByText(/Computed [A-Z][a-z]{2} [A-Z][a-z]{2} \d+/)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(panel).toBeHidden()
}

/** Automated WCAG 2.1 A and AA checks on the page's main region. */
export async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).include('main').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.length} (${v.nodes[0]?.target})`)).toEqual([])
}

export async function expectNoSidewaysScroll(page: Page) {
  // On a failure, name what is too wide.
  const wide = await page.evaluate(() =>
    document.documentElement.scrollWidth <= window.innerWidth + 1
      ? []
      : [...document.querySelectorAll('body *')]
          .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 1 && !el.closest('.table-wrap'))
          .slice(0, 6)
          .map((el) => `${el.tagName.toLowerCase()}.${el.className} right=${Math.round(el.getBoundingClientRect().right)}`),
  )
  expect(wide).toEqual([])
}
