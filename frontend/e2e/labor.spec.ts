import { expect, test, type Page } from '@playwright/test'
import { expectAccessible, expectNoSidewaysScroll, expectPageRules, expectWhyPanel, ML, signIn, part } from './ml'

/** This module's part of the combined page, or the whole document on any other page. */
const view = (page: Page) => part(page, 'labor')

/**
 * Module D, Labor: the week's forecast and people needed, and the standards behind them, in the Labor test
 * kitchen. Every week there is the same: 136 covers a day, 168 on Fridays and Saturdays, and servers clocked
 * so that each handles 8 covers an hour.
 */
const OWNER = { email: 'labor@example.com', password: 'ml-password' }
const STAFF = { email: 'labor-staff@example.com', password: 'ml-password' }
const WEEK = `${ML}/labor/weeks/next`

test.beforeEach(async ({ page }) => signIn(page, OWNER))

test('the week page keeps the page rules and shows expected covers and people needed by role', async ({ page }) => {
  await page.goto('/#labor')
  await expect(view(page).getByRole('heading', { level: 2, name: 'Labor' })).toBeVisible()
  await expect(view(page).getByTestId('header-sentence')).toHaveText(/^Week of [A-Z][a-z]{2} \d+ needs about \d+ hours at expected sales, \d+ if busy\.$/)
  await expectPageRules(view(page))
  await expect(view(page).getByText('Based on 10 weeks of data')).toBeVisible()
  const tiles = view(page).getByTestId('stat-tile')
  await expect(tiles.nth(0)).toContainText('Hours needed')
  await expect(tiles.nth(0)).toContainText('if busy')
  await expect(tiles.nth(1)).toContainText('1,016') // five days of 136 covers and two of 168
  await expect(tiles.nth(1)).toContainText('Expected covers')
  await expect(tiles.nth(1)).toContainText('likely 1,016 to 1,016 for the week') // a range, even when it is narrow
  await expect(tiles.nth(2)).toContainText('Peak people')
  await expect(tiles.nth(2)).toContainText(/Server, (Fri|Sat) /)
  await expectWhyPanel(page, tiles.nth(0).getByRole('button'))

  const chart = view(page).getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText(/^(Friday|Saturday) is the busiest day, about 168 covers$/)
  await expect(chart).toContainText('Last week')
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row')).toHaveCount(8)
  await expect(chart.getByRole('row', { name: /^Fri / })).toContainText('168 to 168')

  const server = view(page).getByRole('row', { name: /Server/ })
  await expect(server).toContainText('hr')
  await expect(server.getByRole('cell').nth(3)).toHaveText('4') // four servers at the Friday and Saturday peak
  await expect(view(page).getByRole('row', { name: /Line cook/i }).getByRole('cell').nth(3)).toHaveText('2')
  // The day view: people needed, and if busy, by half hour, for one role on one day.
  const dayView = view(page).getByTestId('day-view')
  await expect(dayView.getByRole('heading')).toHaveText(/^Server, (Fri|Sat) [A-Z][a-z]{2} \d+: people needed by half hour$/)
  await expect(dayView).toContainText('People needed if busy')
  await view(page).getByRole('row', { name: /Line cook/i }).getByRole('button', { name: 'See day' }).click()
  await expect(dayView.getByRole('heading')).toHaveText(/^Line cook, /i)
  await dayView.getByLabel('Day').selectOption({ index: 0 })
  await expect(dayView.getByRole('heading')).toHaveText(/^Line cook, Mon /i)
  await expectAccessible(page)
  // No wage or cost appears anywhere on the page: hours only.
  expect(await view(page).innerText()).not.toMatch(/\$/)
})

test('the week page says what it is waiting for: setup, learning, loading, an error and stale data', async ({ page }) => {
  const live = await (await page.request.get(WEEK)).json()
  await page.route('**/margin-labor/labor/weeks/next', (route) =>
    route.fulfill({ json: { ...live, state: 'setup', days: [], roles: [], totals: null, intervals: {}, weeks_of_data: 2, setup: [{ key: 'orders', done: false, count: 2, of: 4 }, { key: 'standards', done: false, count: 0 }] } }),
  )
  await page.goto('/#labor')
  await expect(view(page).getByTestId('header-sentence')).toHaveText('0 of 2 setup steps are done.')
  await expect(view(page).getByText('2 of 4 weeks of orders with times are in.')).toBeVisible()
  await expect(view(page).getByRole('link', { name: 'Open standards' })).toBeVisible()
  await expect(view(page).getByTestId('primary-chart')).toHaveCount(0)
  await expect(view(page).getByTestId('stat-tile')).toHaveCount(0)
  await expectAccessible(page)

  await page.unroute('**/margin-labor/labor/weeks/next')
  await page.route('**/margin-labor/labor/weeks/next', (route) => route.fulfill({ json: { ...live, state: 'learning', weeks_of_data: 5 } }))
  await page.reload()
  await expect(view(page).getByRole('status').filter({ hasText: 'Estimate. 5 weeks of data; needs 8.' })).toBeVisible()
  await expect(view(page).getByText('Estimate (5 weeks of data)')).toBeVisible()
  await expect(view(page).getByTestId('stat-tile').first()).toBeVisible()

  await page.unroute('**/margin-labor/labor/weeks/next')
  await page.route('**/margin-labor/labor/weeks/next', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200))
    await route.fulfill({ json: live })
  })
  await page.reload()
  await expect(view(page).getByTestId('skeleton')).toBeVisible()
  await expect(view(page).getByTestId('header-sentence')).toBeVisible()

  await page.unroute('**/margin-labor/labor/weeks/next')
  let fail = true
  await page.route('**/margin-labor/labor/weeks/next', (route) => (fail ? route.fulfill({ status: 500, json: { detail: 'boom' } }) : route.fulfill({ json: live })))
  await page.reload()
  await expect(view(page).getByRole('alert')).toHaveText(/The labor week did not load\. The rest of FullHouse still works\./)
  fail = false
  await view(page).getByRole('button', { name: 'Retry' }).click()
  await expect(view(page).getByTestId('header-sentence')).toBeVisible()

  await page.unroute('**/margin-labor/labor/weeks/next')
  await page.route('**/margin-labor/labor/weeks/next', (route) => route.fulfill({ json: { ...live, stale: true, last_order_date: '2026-11-03' } }))
  await page.reload()
  await expect(view(page).getByRole('status').filter({ hasText: 'Orders were last imported Tue Nov 3. Import a newer export.' })).toBeVisible()
  await expect(view(page).getByTestId('stat-tile').first()).toBeVisible()
})

test('the standards page shows the data behind each standard, and a change says what it does before it is saved', async ({ page }) => {
  await page.goto('/#labor')
  await view(page).getByRole('link', { name: 'Standards' }).click()
  await expect(page).toHaveURL(/#labor\/standards$/)
  await expect(view(page).getByTestId('header-sentence')).toHaveText('2 of 5 roles have a standard. 2 are learned from punches.')
  await expectPageRules(view(page))
  const server = view(page).getByRole('row', { name: /^Server/ })
  await expect(server).toContainText('Covers')
  await expect(server).toContainText('8 an hour')
  await expect(server).toContainText('Learned')
  await expect(server).toContainText(/\d+ of 100 half hours, \d+ weeks of punches/)
  await server.getByRole('button', { name: 'Server' }).click()
  const chart = view(page).getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText('Server: the standard is 8 covers a labor hour')
  await expect(chart).toContainText('Standard 8') // the standard as a labelled line across the observed rates
  await view(page).getByRole('tab', { name: 'Accuracy' }).click()
  await expect(view(page).getByTestId('primary-chart').getByRole('heading')).toHaveText('Dinner has the largest average miss, 2.1%')
  await expect(view(page).getByTestId('range-coverage')).toHaveText('100.0% of days fell inside the likely range, last 8 weeks. The target is 80.0%.')
  await view(page).getByRole('tab', { name: 'Standards' }).click()
  await expectAccessible(page)

  // A slower standard: the effect is shown before saving, and saving changes the people needed.
  const before = await (await page.request.get(WEEK)).json()
  await server.getByRole('button', { name: 'Edit' }).click()
  const editor = view(page).getByRole('dialog', { name: 'Server standard' })
  await expect(editor).toContainText('Learned from punches: 8 an hour')
  await editor.getByLabel('Covers a labor hour').fill('6')
  await expect(editor.getByTestId('standard-preview')).toHaveText(/^Raises (Friday|Saturday) server hours from \d+(\.\d)? to \d+(\.\d)?\.$/)
  await editor.getByLabel('Covers a labor hour').fill('0')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(editor.getByRole('alert')).toHaveText('The standard must be more than 0. The other three are whole numbers, 0 or more.')
  await editor.getByLabel('Covers a labor hour').fill('6')
  await expect(editor.getByTestId('standard-preview')).toBeVisible()
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(editor).toBeHidden()
  await expect(server).toContainText('6 an hour')
  await expect(server).toContainText('Set by you') // the owner's value wins over the learned one
  const after = await (await page.request.get(WEEK)).json()
  const hours = (week: { roles: { role_key: string; hours_p50: number; peak: number }[] }) => week.roles.find((r) => r.role_key === 'server')
  expect(hours(after)!.hours_p50).toBeGreaterThan(hours(before)!.hours_p50)
  expect(hours(after)!.peak).toBe(6) // 16 covers a half hour at 3 per server
  await page.goto('/#labor')
  await expect(view(page).getByRole('row', { name: /Server/ }).getByRole('cell').nth(3)).toHaveText('6')

  // Back to the learned value.
  await page.goto('/#labor/standards')
  await view(page).getByRole('row', { name: /^Server/ }).getByRole('button', { name: 'Edit' }).click()
  await editor.getByRole('button', { name: 'Use learned' }).click()
  await expect(view(page).getByRole('row', { name: /^Server/ })).toContainText('8 an hour')
  await expect(view(page).getByRole('row', { name: /^Server/ })).toContainText('Learned')
})

test('staff can read labor but cannot change a standard, and a phone shows one column', async ({ page }) => {
  await signIn(page, STAFF)
  await page.goto('/#labor/standards')
  await expect(view(page).getByRole('row', { name: /^Server/ })).toContainText('8 an hour')
  await expect(view(page).getByRole('button', { name: 'Edit' })).toHaveCount(0)
  expect((await page.request.put(`${ML}/labor/standards`, { data: { role_key: 'server', units_per_labor_hour: '5' } })).status()).toBe(403)
  expect((await page.request.post(`${ML}/labor/standards/preview`, { data: { role_key: 'server', units_per_labor_hour: '5' } })).status()).toBe(403)
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/#labor')
  await expect(view(page).getByTestId('header-sentence')).toBeVisible()
  await expectNoSidewaysScroll(page)
})
