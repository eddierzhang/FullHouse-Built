import { expect, test, type Page } from '@playwright/test'
import { expectAccessible, expectNoSidewaysScroll, expectPageRules, expectWhyPanel, ML, ML_STAFF, signIn, part } from './ml'

/** This module's part of the combined page, or the whole document on any other page. */
const view = (page: Page) => part(page, 'usage')

/**
 * Module B, Usage, in the Margin & Labor test kitchen. It buys 40 kg of wings a week and its recipes use 28:
 * after one 20 kg delivery of slack, 28 kg in 28 days is unexplained, $49.00 a week.
 */
const LIVE = `${ML}/usage`

test.beforeEach(async ({ page }) => signIn(page))

test('the list page keeps the page rules and explains every number', async ({ page }) => {
  await page.goto('/#usage')
  await expect(view(page).getByRole('heading', { level: 2, name: 'Usage' })).toBeVisible()
  await expect(view(page).getByTestId('header-sentence')).toHaveText('About $49.00 a week of ingredients is unexplained, across 1 item.')
  await expectPageRules(view(page))
  const wings = view(page).getByRole('row', { name: /Chicken wings/ })
  await expect(wings).toContainText('112 kg')
  await expect(wings).toContainText('160 kg')
  await expect(wings).toContainText('28 kg, 25.0%')
  await expect(wings).toContainText('$49.00')
  await expect(view(page).locator('.ml-table tbody tr')).toHaveCount(1) // ingredients with no gap are not rows

  await wings.getByRole('button', { name: '$49.00' }).click()
  const panel = view(page).getByRole('dialog', { name: 'How this was worked out' })
  await expect(panel).toContainText('$49.00 a week')
  await expect(panel).toContainText('Unexplained use = purchased - expected use - one typical delivery')
  await expect(panel).toContainText('112,000 g')
  await expect(panel).toContainText(/Median of 1[67] deliveries, last 8 weeks/) // the kitchen's first small invoice is inside the 8 weeks on some weekdays
  await expect(panel).toContainText('recipe coverage 100.0%')
  await page.keyboard.press('Escape')
  await expectWhyPanel(page, wings.getByRole('button', { name: '$49.00' }))
  for (const tile of await view(page).getByTestId('stat-tile').getByRole('button').all()) await expectWhyPanel(page, tile)

  const chart = view(page).getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText('Chicken wings is the largest gap, $49.00 a week')
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row', { name: /Chicken wings \$49.00/ })).toBeVisible()
  await expectAccessible(page)
})

test('the page says what it is waiting for: setup, learning, loading, an error, stale data and an empty result', async ({ page }) => {
  const live = await (await page.request.get(LIVE)).json()
  const soda = [{ pos_item_id: 'SODA', name: 'Soda', net: '8400', share: '0.295276', has_recipe: false }]

  // Setup: coverage against what is needed, and the top sellers that need a recipe. No variance numbers.
  await page.route('**/margin-labor/usage', (route) => route.fulfill({ json: { ...live, state: 'setup', coverage: '0.704724', items: [], header: null, tiles: null, missing_recipes: soda, recipes_needed: 1 } }))
  await page.goto('/#usage')
  await expect(view(page).getByTestId('header-sentence')).toHaveText('Recipes cover 70.5% of sales. Usage needs 80.0%.')
  await expect(view(page).getByTestId('coverage-bar')).toContainText('Recipes cover 70.5% of sales. Findings need 90.0%.')
  await expect(view(page).getByText('Add a recipe for Soda')).toBeVisible()
  await expect(view(page).getByText('29.5% of sales, last 28 days')).toBeVisible()
  await expect(view(page).getByRole('link', { name: 'Open recipes' })).toBeVisible()
  await expect(view(page).getByTestId('primary-chart')).toHaveCount(0)
  await expect(view(page).getByTestId('stat-tile')).toHaveCount(0)
  await expect(view(page).getByText('$49.00')).toHaveCount(0)
  await expectAccessible(page)

  // Learning: the table, labelled an estimate, under a banner that says what is needed.
  await page.unroute('**/margin-labor/usage')
  await page.route('**/margin-labor/usage', (route) => route.fulfill({ json: { ...live, state: 'learning', coverage: '0.850003', missing_recipes: soda, recipes_needed: 1, items: live.items.map((i: object) => ({ ...i, finding: null })) } }))
  await page.reload()
  await expect(view(page).getByRole('status').filter({ hasText: 'Estimate. Recipe coverage is 85.0%; findings need 90.0%.' })).toBeVisible()
  await expect(view(page).getByTestId('coverage-bar')).toBeVisible()
  await expect(view(page).getByRole('row', { name: /Chicken wings/ })).toContainText('Estimate')
  await expect(view(page).getByRole('row', { name: /Chicken wings/ })).toContainText('$49.00')
  await expectAccessible(page)

  // Loading, then an error with Retry.
  await page.unroute('**/margin-labor/usage')
  await page.route('**/margin-labor/usage', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200))
    await route.fulfill({ json: live })
  })
  await page.reload()
  await expect(view(page).getByTestId('skeleton')).toBeVisible()
  await expect(view(page).getByTestId('header-sentence')).toBeVisible()
  await page.unroute('**/margin-labor/usage')
  let fail = true
  await page.route('**/margin-labor/usage', (route) => (fail ? route.fulfill({ status: 500, json: { detail: 'boom' } }) : route.fulfill({ json: live })))
  await page.reload()
  await expect(view(page).getByRole('alert')).toHaveText(/Usage did not load\. The rest of FullHouse still works\./)
  fail = false
  await view(page).getByRole('button', { name: 'Retry' }).click()
  await expect(view(page).getByTestId('header-sentence')).toBeVisible()

  // Stale: the banner and the fix, with the numbers still there.
  await page.unroute('**/margin-labor/usage')
  await page.route('**/margin-labor/usage', (route) => route.fulfill({ json: { ...live, stale: true, last_sales_date: '2026-11-03' } }))
  await page.reload()
  await expect(view(page).getByRole('status').filter({ hasText: 'Sales were last imported Tue Nov 3. Import a newer export.' })).toBeVisible()
  await expect(view(page).getByRole('row', { name: /Chicken wings/ })).toContainText('$49.00')

  // Empty: one sentence stating the fact, and no chart or table. Small gaps are one line.
  await page.unroute('**/margin-labor/usage')
  await page.route('**/margin-labor/usage', (route) =>
    route.fulfill({ json: { ...live, items: [], header: { weekly_dollars: '0', items: 0 }, more_under: { count: 12, threshold: '5' }, tiles: { ...live.tiles, unexplained: { value: '0', then: '0' }, flagged: { value: 0, then: 0 } } } }),
  )
  await page.reload()
  await expect(view(page).getByTestId('header-sentence')).toHaveText('No ingredients are unexplained in the last 28 days.')
  await expect(view(page).getByTestId('primary-chart')).toHaveCount(0)
  await expect(view(page).locator('.ml-table')).toHaveCount(0)
  await expect(view(page).getByTestId('collapsed-rows')).toHaveText('12 more items under $5.00 a week.')
})

test('an ingredient page shows eight windows, the dishes that use it and the three checks', async ({ page }) => {
  await page.goto('/#usage')
  await view(page).getByRole('link', { name: 'Chicken wings' }).click()
  await expect(page).toHaveURL(/#usage\/[0-9a-f]{32}$/)
  await expect(view(page).getByTestId('header-sentence')).toHaveText('Chicken wings: about $49.00 a week is unexplained, 25.0% of expected use.')
  await expectPageRules(view(page))
  const tiles = view(page).getByTestId('stat-tile')
  await expect(tiles.nth(0)).toContainText('112 kg')
  await expect(tiles.nth(1)).toContainText('160 kg')
  await expect(tiles.nth(1)).toContainText('8 deliveries, last 28 days')
  await expect(tiles.nth(2)).toContainText('28 kg')
  await expect(tiles.nth(2)).toContainText('after 1 delivery of 20 kg')
  const chart = view(page).getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText('Purchases ran past expected use and a delivery in 8 of 8 windows')
  await expect(chart).toContainText('Purchased')
  await expect(chart).toContainText('Expected use')
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row')).toHaveCount(9)
  await expect(chart.getByRole('row').last()).toContainText('$49.00')
  await expect(view(page).getByTestId('dishes-using').getByRole('heading')).toHaveText('Wings uses the most, 112 kg in 28 days')
  const checks = view(page).getByRole('region', { name: 'Checks' })
  await expect(checks).toContainText('Weigh five portions of Wings. The recipe says 400 g a portion.')
  await expect(checks).toContainText(/Check the [A-Z][a-z]{2} [A-Z][a-z]{2} \d+ delivery against its invoice\./)
  await expect(checks).toContainText('Review the recipe quantity.')
  await expectWhyPanel(page, tiles.nth(2).getByRole('button'))
  await expectAccessible(page)
  await view(page).getByRole('link', { name: 'Usage' }).first().click()
  await expect(page).toHaveURL(/#usage$/)
})

test('staff can read usage but cannot dismiss a finding or stop tracking', async ({ page }) => {
  await signIn(page, ML_STAFF)
  await page.goto('/#usage')
  await view(page).getByRole('row', { name: /Chicken wings/ }).getByRole('button', { name: 'See checks' }).click()
  const panel = view(page).getByRole('dialog', { name: 'Chicken wings' })
  await expect(panel).toContainText('Weigh five portions of Wings.')
  await expect(panel.getByRole('button', { name: 'Dismiss' })).toHaveCount(0)
  const item = (await (await page.request.get(LIVE)).json()).items[0]
  expect((await page.request.put(`${LIVE}/${item.item_id}/tracking`, { data: { track_usage: false } })).status()).toBe(403)
  expect((await page.request.post(`${ML}/findings/${item.finding.id}/dismiss`)).status()).toBe(403)
})

test('on a phone the list is one column with no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/#usage')
  await expect(view(page).getByTestId('header-sentence')).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(await view(page).locator('.ml-table thead th:visible').count()).toBeLessThanOrEqual(3)
})

test('from the digest card it is two clicks to the checks, and a dismissal can be undone', async ({ page }) => {
  test.setTimeout(90_000) // the dismissal is sent after its ten seconds of Undo
  await page.goto('/#digest')
  const card = view(page).getByTestId('module-card-usage')
  await expect(card).toContainText('About $49.00 a week of ingredients is unexplained, across 1 item.')
  await card.getByRole('link', { name: 'See checks' }).click()
  const panel = view(page).getByRole('dialog', { name: 'Chicken wings' })
  await expect(panel).toContainText('About $49.00 a week is unexplained.')
  await expect(panel).toContainText('FullHouse cannot tell waste from over-portioning or theft.')
  await expect(panel.locator('ol li')).toHaveCount(3)
  await expect(view(page).getByRole('row', { name: /Chicken wings/ })).toBeVisible() // the list stays in view

  await panel.getByRole('button', { name: 'Dismiss' }).click()
  const bar = view(page).getByRole('status').filter({ hasText: 'Chicken wings finding dismissed.' })
  await bar.getByRole('button', { name: 'Undo' }).click()
  const wings = view(page).getByRole('row', { name: /Chicken wings/ })
  await expect(wings.getByRole('button', { name: 'See checks' })).toBeVisible()
  expect((await (await page.request.get(LIVE)).json()).items[0].finding).not.toBeNull()

  // By keyboard this time, and left to stand.
  await wings.getByRole('button', { name: 'See checks' }).focus()
  await page.keyboard.press('Enter')
  await panel.getByRole('button', { name: 'Dismiss' }).focus()
  await page.keyboard.press('Enter')
  await expect(bar).toBeVisible()
  await expect(async () => {
    const body = await (await page.request.get(LIVE)).json()
    expect(body.tiles.flagged.value).toBe(0)
    expect(body.items[0].finding).toBeNull()
  }).toPass({ timeout: 45_000 })
  // The gap is still on the page (it is still true); only the finding is set aside, and the digest card goes.
  await page.reload()
  await expect(view(page).getByRole('row', { name: /Chicken wings/ })).toContainText('$49.00')
  const cards = page.waitForResponse((r) => r.url().includes('/margin-labor/digest-cards'))
  await page.goto('/#digest')
  expect(((await (await cards).json()) as { module: string }[]).map((c) => c.module)).not.toContain('usage')
  await expect(view(page).getByTestId('module-card-usage')).toHaveCount(0)
})
