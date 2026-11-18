import { expect, test } from '@playwright/test'
import { API, expectAccessible, expectNoSidewaysScroll, expectPageRules, expectWhyPanel, ML, ML_STAFF, signIn } from './ml'

/**
 * Module E, Channels, in the Margin & Labor test kitchen. Fries sell for $5.00 on Uber Eats, which takes 33% and
 * $2.00 an order, with $1.00 of packaging: each loses 8.5 cents. Burgers and wings do well on DoorDash, where two
 * orders in three have a payout row and the third falls back to the contract rates.
 */
const LIVE = `${ML}/channels`

test.beforeEach(async ({ page }) => signIn(page))

test('the page keeps the page rules, shows the match rate, and explains every number', async ({ page }) => {
  await page.goto('/#channels')
  await expect(page.getByRole('heading', { level: 1, name: 'Channels' })).toBeVisible()
  await expect(page.getByTestId('header-sentence')).toHaveText('1 item loses money on Uber Eats. Cost: about $32.55 a week.')
  await expectPageRules(page)
  // Half of the platform orders matched a payout report: under 80%, so the page says what it rests on.
  await expect(page.getByRole('status').filter({ hasText: 'Based on your contract rates. 50.0% of platform orders matched a payout report.' })).toBeVisible()
  const tiles = page.getByTestId('stat-tile')
  await expect(tiles).toHaveCount(3)
  await expect(tiles.nth(0)).toContainText('78.1%')
  await expect(tiles.nth(0)).toContainText('Dine in')
  await expect(tiles.nth(1)).toContainText('47.8%')
  await expect(tiles.nth(1)).toContainText('DoorDash')
  await expect(tiles.nth(2)).toContainText('−1.7%')
  await expect(tiles.nth(2)).toContainText('Uber Eats')
  for (const tile of await tiles.getByRole('button').all()) await expectWhyPanel(page, tile)

  const fries = page.getByRole('row', { name: /Fries/ })
  await expect(page.locator('.ml-table tbody tr').first()).toContainText('Fries') // largest dollars a week first
  await expect(fries).toContainText('Uber Eats')
  await expect(fries).toContainText('$5.00')
  await expect(fries).toContainText('$4.65') // commission, processing, fixed fee and packaging
  await expect(fries).toContainText('$0.44')
  await expect(fries).toContainText('−$0.09')
  await expect(fries).toContainText('Loses money') // a word and an icon, not a colour
  await expect(page.getByRole('row', { name: /Burger/ })).toContainText('$6.50')

  await fries.getByRole('button', { name: '−$0.09' }).click()
  const panel = page.getByRole('dialog', { name: 'How this was worked out' })
  await expect(panel).toContainText('−$0.09 kept per item on Uber Eats')
  await expect(panel).toContainText('Kept per item = price - commission - processing - fixed fees and packaging - cost per plate')
  await expect(panel).toContainText('Contract rates')
  await expect(panel).toContainText('28 orders in 28 days')
  await expect(panel.getByRole('img', { name: /Price \$5\.00, less fees and food cost, leaves −\$0\.09/ })).toBeVisible() // the waterfall
  await page.keyboard.press('Escape')
  await expectWhyPanel(page, page.getByRole('row', { name: /Burger/ }).getByRole('button', { name: '$6.50' }))

  const chart = page.getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText('Fries keeps −$0.09 on Uber Eats')
  await expect(chart).toContainText('dollars kept per item')
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row', { name: /Burger \$9\.56 \$6\.50 Not sold/ })).toBeVisible()
  await expectAccessible(page)
})

test('the page says what it is waiting for: setup, loading, an error, stale data, an empty result and matched payouts', async ({ page }) => {
  const live = await (await page.request.get(LIVE)).json()

  await page.route('**/margin-labor/channels', (route) =>
    route.fulfill({ json: { ...live, state: 'setup', items: [], header: null, tiles: [], chart: null, setup: [{ key: 'menu_costs', done: true, count: 1 }, { key: 'orders', done: true, count: 168 }, { key: 'rates', done: false, count: 1, of: 2, missing: ['Uber Eats'] }] } }),
  )
  await page.goto('/#channels')
  await expect(page.getByTestId('header-sentence')).toHaveText('2 of 3 setup steps are done.')
  await expect(page.getByText('Set rates for Uber Eats')).toBeVisible()
  await expect(page.getByTestId('primary-chart')).toHaveCount(0)
  await expect(page.getByTestId('stat-tile')).toHaveCount(0)
  await page.getByRole('button', { name: 'Set rates' }).click()
  const editor = page.getByRole('dialog', { name: 'Platform rates' })
  await expect(editor.getByLabel('Platform')).toHaveValue('Uber Eats')
  await editor.getByLabel('Commission, %').fill('80')
  await editor.getByLabel('Processing, %').fill('30')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(editor.getByRole('alert')).toHaveText('Name the platform. Commission and processing are percentages that total under 100.')
  await page.keyboard.press('Escape')
  await expectAccessible(page)

  await page.unroute('**/margin-labor/channels')
  await page.route('**/margin-labor/channels', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200))
    await route.fulfill({ json: live })
  })
  await page.reload()
  await expect(page.getByTestId('skeleton')).toBeVisible()
  await expect(page.getByTestId('header-sentence')).toBeVisible()

  await page.unroute('**/margin-labor/channels')
  let fail = true
  await page.route('**/margin-labor/channels', (route) => (fail ? route.fulfill({ status: 500, json: { detail: 'boom' } }) : route.fulfill({ json: live })))
  await page.reload()
  await expect(page.getByRole('alert')).toHaveText(/Channels did not load\. The rest of FullHouse still works\./)
  fail = false
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByTestId('header-sentence')).toBeVisible()

  await page.unroute('**/margin-labor/channels')
  await page.route('**/margin-labor/channels', (route) => route.fulfill({ json: { ...live, stale: true, last_order_date: '2026-11-03' } }))
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: 'Orders were last imported Tue Nov 3. Import a newer export.' })).toBeVisible()
  await expect(page.getByRole('row', { name: /Fries/ })).toContainText('−$0.09')

  // Empty, with payouts matched: the fact, and the match rate as a plain line.
  await page.unroute('**/margin-labor/channels')
  await page.route('**/margin-labor/channels', (route) =>
    route.fulfill({ json: { ...live, match_rate: '0.920000', contract_rates: false, header: { ...live.header, items: 0, weekly_dollars: '0', platform: null, losing: false }, items: live.items.map((i: object) => ({ ...i, finding: null })) } }),
  )
  await page.reload()
  await expect(page.getByTestId('header-sentence')).toHaveText('No items lose money on delivery platforms, last 28 days.')
  await expect(page.getByTestId('match-rate')).toHaveText('92.0% of platform orders matched a payout report.')
  await expect(page.getByRole('button', { name: 'Review price' })).toHaveCount(0)
})

test('staff can read channels but cannot review a price or change rates', async ({ page }) => {
  await signIn(page, ML_STAFF)
  await page.goto('/#channels')
  await expect(page.getByRole('row', { name: /Fries/ })).toContainText('−$0.09')
  await expect(page.getByRole('button', { name: 'Review price' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Edit rates' })).toHaveCount(0)
  expect((await page.request.put(`${LIVE}/terms`, { data: { platform: 'Uber Eats', commission_pct: '0.1' } })).status()).toBe(403)
})

test('on a phone the page is one column with no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/#channels')
  await expect(page.getByTestId('header-sentence')).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(await page.locator('.ml-table thead th:visible').count()).toBeLessThanOrEqual(3)
})

test('from the digest card it is two clicks to approve a platform price, with the fee waterfall beside it', async ({ page }) => {
  test.setTimeout(90_000)
  await page.goto('/#digest')
  const card = page.getByTestId('module-card-channels')
  await expect(card).toContainText('1 item loses money on Uber Eats.')
  await expect(card).toContainText('$32.55 a week')
  await card.getByRole('link', { name: 'Review price' }).click()
  const review = page.getByRole('dialog', { name: 'Fries on Uber Eats' })
  await expect(review).toContainText('Price it at $12.00 on Uber Eats')
  await expect(review).toContainText('Fries keeps −$0.09 an item on Uber Eats at $5.00.')
  await expect(review).toContainText('$12.00 keeps $4.61, against a target of $4.57. Check your agreement with the platform before changing prices.')
  await expect(review).toContainText('$32.55 a week')
  await expect(review.getByTestId('waterfall')).toContainText('−$1.50') // commission, as a step down
  await expect(page.getByRole('row', { name: /Fries/ })).toBeVisible()

  await review.getByRole('button', { name: 'Dismiss' }).click()
  await page.getByRole('status').filter({ hasText: 'Fries price dismissed.' }).getByRole('button', { name: 'Undo' }).click()
  const fries = page.getByRole('row', { name: /Fries/ })
  await fries.getByRole('button', { name: 'Review price' }).focus()
  await page.keyboard.press('Enter')
  await review.getByRole('button', { name: 'Approve' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Fries price approved.' })).toBeVisible()
  await expect(async () => {
    const actions = await (await page.request.get(`${API}/api/v1/agents/actions?status=approved`)).json()
    expect(actions.filter((a: { action_type: string }) => a.action_type === 'channel_reprice')).toHaveLength(1)
  }).toPass({ timeout: 45_000 })
  await expect(fries).toContainText('Approved')
  // Nothing was changed on any platform: the fries still sell for $5.00 there until a person changes it.
  await page.reload()
  await expect(page.getByRole('row', { name: /Fries/ })).toContainText('$5.00')
  await expect(page.getByRole('row', { name: /Fries/ })).toContainText('Approved')

  // New rates for Uber Eats start today; the window's orders keep the rates they were sold under.
  await page.getByRole('button', { name: 'Edit rates' }).click()
  const editor = page.getByRole('dialog', { name: 'Platform rates' })
  await editor.getByLabel('Platform').fill('Uber Eats')
  await editor.getByLabel('Commission, %').fill('20')
  await editor.getByLabel('Processing, %').fill('3')
  await editor.getByLabel('Fixed fee an order, $').fill('0.5')
  await editor.getByLabel('Packaging an order, $').fill('1')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(editor).toBeHidden()
  const terms = (await (await page.request.get(LIVE)).json()).terms.find((t: { platform: string }) => t.platform === 'Uber Eats')
  expect(terms.commission_pct).toBe('0.200000')
  await expect(page.getByRole('row', { name: /Fries/ })).toContainText('−$0.09')
})
