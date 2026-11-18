import { expect, test } from '@playwright/test'
import { API, expectAccessible, expectNoSidewaysScroll, expectPageRules, expectWhyPanel, ML, ML_STAFF, signIn } from './ml'

/**
 * Forecast core and Module C, Ordering and prep, in the Margin & Labor test kitchen. It sells the same 78 items
 * every day, so the forecast has no range; Margin Farm delivers Thursdays and Mondays. The spec's flow 4: a draft
 * is opened, one line is corrected with "We have plenty", and the draft is approved.
 */
const LIVE = `${ML}/ordering/drafts`

test.beforeEach(async ({ page }) => signIn(page))

test('the page keeps the page rules and shows demand, the next cutoff and the average miss', async ({ page }) => {
  await page.goto('/#ordering-prep')
  await expect(page.getByRole('heading', { level: 1, name: 'Ordering and prep' })).toBeVisible()
  await expect(page.getByTestId('header-sentence')).toHaveText(/^0 orders to review\. Next cutoff: Margin Farm, [A-Z][a-z]{2} [A-Z][a-z]{2} \d+ \d+:\d\d [ap]m\.$/)
  await expectPageRules(page)
  await expect(page.getByText('Based on 12 weeks of data')).toBeVisible() // confidence in words, never a bare percentage
  const tiles = page.getByTestId('stat-tile')
  await expect(tiles.nth(0)).toContainText('Orders waiting')
  await expect(tiles.nth(1)).toContainText('Next cutoff')
  await expect(tiles.nth(1)).toContainText('Margin Farm, delivery')
  await expect(tiles.nth(2)).toContainText('0.0%')
  await expect(tiles.nth(2)).toContainText('Average miss')
  await expectWhyPanel(page, tiles.nth(2).getByRole('button'))

  const chart = page.getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText('About 78 items a day are expected over the next 14 days')
  await expect(chart).toContainText('Now') // the line between what happened and what is expected
  await expect(chart).toContainText('Likely range')
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row')).toHaveCount(29)
  await expect(chart.getByRole('row').last()).toContainText('78 to 78')
  await page.getByRole('tab', { name: 'Accuracy' }).click()
  await expect(page.getByTestId('primary-chart')).toHaveCount(1)
  await expect(page.getByTestId('primary-chart').getByRole('heading')).toHaveText('Average miss was 0.0% over the last 28 days')
  await page.getByRole('tab', { name: 'Demand' }).click()
  await expectAccessible(page)
})

test('the page says what it is waiting for: setup, loading, an error and stale data', async ({ page }) => {
  const live = await (await page.request.get(LIVE)).json()
  await page.route('**/margin-labor/ordering/drafts', (route) =>
    route.fulfill({ json: { ...live, state: 'setup', header: null, tiles: null, chart: null, drafts: [], setup: [{ key: 'costs', done: true, count: 0 }, { key: 'schedule', done: false, count: 0 }, { key: 'sales', done: false, count: 2, of: 4 }] } }),
  )
  await page.goto('/#ordering-prep')
  await expect(page.getByTestId('header-sentence')).toHaveText('1 of 3 setup steps are done.')
  await expect(page.getByText('2 of 4 weeks are in.')).toBeVisible()
  await expect(page.getByTestId('primary-chart')).toHaveCount(0)
  await expect(page.getByTestId('stat-tile')).toHaveCount(0)
  await page.getByRole('button', { name: 'Add delivery days' }).click()
  const days = page.getByRole('dialog', { name: 'Delivery days' })
  await expect(days.getByLabel('Supplier')).toHaveValue(live.suppliers[0].id)
  await days.getByLabel('Cutoff time').fill('2pm')
  await days.getByRole('button', { name: 'Save' }).click()
  await expect(days.getByRole('alert')).toHaveText('Pick a supplier, and give the cutoff as a 24-hour time such as 14:00.')
  await page.keyboard.press('Escape')
  await expectAccessible(page)

  await page.unroute('**/margin-labor/ordering/drafts')
  await page.route('**/margin-labor/ordering/drafts', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200))
    await route.fulfill({ json: live })
  })
  await page.reload()
  await expect(page.getByTestId('skeleton')).toBeVisible()
  await expect(page.getByTestId('header-sentence')).toBeVisible()

  await page.unroute('**/margin-labor/ordering/drafts')
  let fail = true
  await page.route('**/margin-labor/ordering/drafts', (route) => (fail ? route.fulfill({ status: 500, json: { detail: 'boom' } }) : route.fulfill({ json: live })))
  await page.reload()
  await expect(page.getByRole('alert')).toHaveText(/Ordering and prep did not load\. The rest of FullHouse still works\./)
  fail = false
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByTestId('header-sentence')).toBeVisible()

  await page.unroute('**/margin-labor/ordering/drafts')
  await page.route('**/margin-labor/ordering/drafts', (route) => route.fulfill({ json: { ...live, stale: true, last_sales_date: '2026-11-03' } }))
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: 'Sales were last imported Tue Nov 3. Import a newer export.' })).toBeVisible()
  await expect(page.getByTestId('stat-tile').first()).toBeVisible()
})

test('the prep sheet lists each prepped item with its par and two blank columns to fill on paper', async ({ page }) => {
  await page.goto('/#ordering-prep')
  await page.getByRole('link', { name: 'Prep sheet' }).click()
  await expect(page).toHaveURL(/#ordering-prep\/prep\/\d{4}-\d{2}-\d{2}$/)
  await expect(page.getByTestId('header-sentence')).toHaveText(/^Prep for [A-Z][a-z]{2} [A-Z][a-z]{2} \d+: 1 item to par\.$/)
  const station = page.getByRole('region', { name: 'Kitchen prep' })
  await expect(station.getByRole('columnheader')).toHaveText(['Item', 'Par', 'Batch size', 'Shelf life', 'On hand', 'Prep'])
  const sauce = station.getByRole('row', { name: /House sauce/ })
  await expect(sauce).toContainText('600 g') // 30 burgers at 20 g each
  await expect(sauce).toContainText('500 g')
  await expect(sauce).toContainText('For Burger')
  await expect(sauce.locator('.ml-blank')).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Print' })).toBeVisible()
  await page.getByRole('button', { name: 'Propose sheet' }).click()
  await expect(page.getByText('Waiting for approval')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Propose sheet' })).toHaveCount(0) // one per business date
  await expectAccessible(page)
})

test('flow 4: a draft order is opened, one line is corrected with "We have plenty", and the draft is approved', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/#ordering-prep')
  await page.getByRole('button', { name: 'Draft order' }).click()
  const draft = page.getByTestId('order-draft')
  await expect(draft.getByRole('heading')).toHaveText(/^Margin Farm, delivery [A-Z][a-z]{2} [A-Z][a-z]{2} \d+$/)
  await expect(page.getByTestId('header-sentence')).toHaveText(/^1 order to review\. Next cutoff: Margin Farm, /)
  await expect(draft).toContainText('Check quantities') // the stock estimate is new
  await expect(draft.locator('tbody tr')).toHaveCount(8)
  await expectPageRules(page)
  await expect(page.getByRole('button', { name: 'Draft order' })).toHaveCount(0)

  // Every line shows what it rests on, with the estimated stock through the delivery period.
  const wings = draft.getByRole('row', { name: /Chicken wings/ })
  await expect(wings).toContainText('case of 1 kg')
  await wings.getByRole('button', { name: /kg$/ }).click()
  const panel = page.getByRole('dialog', { name: 'How this was worked out' })
  await expect(panel).toContainText('case of Chicken wings suggested')
  await expect(panel).toContainText('rounded up to whole packs')
  await expect(panel).toContainText('Estimated on hand at delivery')
  await expect(panel).toContainText('not a count')
  await expect(panel.getByRole('img', { name: /Estimated stock by week/ })).toBeVisible()
  await page.keyboard.press('Escape')

  // "We have plenty": the line goes to zero and the estimate becomes the need. A correction, not a count.
  const beef = draft.getByRole('row', { name: /Ground beef/ })
  await beef.getByRole('button', { name: 'We have plenty' }).click()
  await expect(beef.getByLabel('Packs of Ground beef')).toHaveValue('0')
  await expect(beef).toContainText('Marked plenty')
  await expect(beef).toContainText('$0.00')
  // "We're out": the whole need is ordered.
  const before = Number(await wings.getByLabel('Packs of Chicken wings').inputValue())
  await wings.getByRole('button', { name: "We're out" }).click()
  await expect(wings).toContainText('Marked out')
  const out = Number(await wings.getByLabel('Packs of Chicken wings').inputValue())
  expect(out).toBeGreaterThanOrEqual(before)
  expect([14, 19]).toContain(out) // three or four days at 4 kg a day, plus Usage's 15% loss allowance for wings, in 1 kg cases
  // A person's own quantity is kept beside what was suggested.
  await wings.getByLabel('Packs of Chicken wings').fill(String(out - 2))
  await wings.getByLabel('Packs of Chicken wings').press('Enter')
  await expect(wings).toContainText(`Suggested ${before}`)

  // The digest card leads here; the order is approved by keyboard, after an Undo.
  await page.goto('/#digest')
  const card = page.getByTestId('module-card-ordering')
  await expect(card).toContainText(/1 order to review\. First: Margin Farm, by /)
  await card.getByRole('link', { name: 'Review order' }).click()
  await draft.getByRole('button', { name: 'Dismiss' }).click()
  await page.getByRole('status').filter({ hasText: 'Margin Farm order dismissed.' }).getByRole('button', { name: 'Undo' }).click()
  await draft.getByRole('button', { name: 'Approve' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Margin Farm order approved.' })).toBeVisible()
  let approved: { action_type: string; applied_result: string; payload: { module?: string } }[] = []
  await expect(async () => {
    approved = (await (await page.request.get(`${API}/api/v1/agents/actions?status=approved`)).json()).filter((a: { action_type: string; payload: { module?: string } }) => a.action_type === 'purchase_order' && a.payload.module === 'ordering')
    expect(approved).toHaveLength(1)
  }).toPass({ timeout: 45_000 })
  await expect(draft).toContainText('Approved. The order is a draft; you send it.')
  // Approving drafted the order and sent nothing: a person sends it from the order's page.
  expect(approved[0].applied_result).toMatch(/Drafted an order of \d lines? to Margin Farm/) // the beef line, set to zero, is not on it
  expect(approved[0].applied_result).toContain('Nothing has been sent.')
  await page.reload()
  await expect(page.getByTestId('header-sentence')).toHaveText(/^0 orders to review\./)
  await expect(page.getByTestId('order-draft')).toHaveCount(0)
})

test('staff can read the page but cannot draft, correct or approve an order', async ({ page }) => {
  await signIn(page, ML_STAFF)
  await page.goto('/#ordering-prep')
  await expect(page.getByTestId('header-sentence')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Draft order' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Delivery days' })).toHaveCount(0)
  const live = await (await page.request.get(LIVE)).json()
  expect((await page.request.post(LIVE, { data: { supplier_id: live.suppliers[0].id } })).status()).toBe(403)
  expect((await page.request.put(`${ML}/ordering/schedules`, { data: { supplier_id: live.suppliers[0].id, order_cutoff_weekday: 1, order_cutoff_time: '10:00', delivery_weekday: 2 } })).status()).toBe(403)
})

test('delivery days can be added and removed, and a phone shows one column', async ({ page }) => {
  await page.goto('/#ordering-prep')
  await page.getByRole('button', { name: 'Delivery days' }).click()
  const days = page.getByRole('dialog', { name: 'Delivery days' })
  await expect(days.getByText('Margin Farm: Thursday')).toBeVisible()
  await expect(days.getByText('Order by Wednesday 14:00, minimum $150')).toBeVisible()
  await days.getByLabel('Delivery day').selectOption('Saturday')
  await days.getByLabel('Order cutoff day').selectOption('Friday')
  await days.getByLabel('Cutoff time').fill('11:30')
  await days.getByRole('button', { name: 'Save' }).click()
  await expect(days.getByText('Margin Farm: Saturday')).toBeVisible()
  await expect(days.getByText('Order by Friday 11:30')).toBeVisible()
  await days.getByRole('button', { name: 'Remove Margin Farm Saturday' }).click()
  await expect(days.getByText('Margin Farm: Saturday')).toHaveCount(0)
  await page.keyboard.press('Escape')

  await page.setViewportSize({ width: 375, height: 800 })
  await page.reload()
  await expect(page.getByTestId('header-sentence')).toBeVisible()
  await expectNoSidewaysScroll(page)
})
