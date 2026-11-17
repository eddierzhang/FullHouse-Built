import { expect, test } from '@playwright/test'
import { API, expectAccessible, expectNoSidewaysScroll, expectPageRules, expectWhyPanel, ML, ML_STAFF, signIn } from './ml'

/**
 * Module A, Menu costs, in the Margin & Labor test kitchen (its own restaurant): the page rules, each stored
 * state, and the spec's end-to-end flow 1. The burger starts at $2.44 a plate, 20.3% of $12.00.
 */
const LIVE = `${ML}/menu-costs`

test.beforeEach(async ({ page }) => signIn(page))

test('the list page keeps the page rules and explains every number', async ({ page }) => {
  await page.goto('/#menu-costs')
  await expect(page.getByRole('heading', { level: 1, name: 'Menu costs' })).toBeVisible()
  await expect(page.getByTestId('header-sentence')).toHaveText('No dishes are over your 30.0% food cost target.')
  await expectPageRules(page)
  const burger = page.getByRole('row', { name: /Burger/ })
  await expect(burger).toContainText('$12.00')
  await expect(burger).toContainText('$2.44')
  await expect(burger).toContainText('20.3%')
  await expect(burger).toContainText('840')
  // Rows are sorted and every dish with a recipe is there; the sub-recipe is not a dish.
  await expect(page.locator('.ml-table tbody tr')).toHaveCount(4)
  await expect(page.getByRole('row', { name: /House sauce/ })).toHaveCount(0)

  await expectWhyPanel(page, burger.getByRole('button', { name: '$2.44' }))
  await burger.getByRole('button', { name: '$2.44' }).click()
  const panel = page.getByRole('dialog', { name: 'How this was worked out' })
  await expect(panel).toContainText('$2.44 a plate')
  await expect(panel).toContainText("Cost per plate = sum of each ingredient's quantity x its latest price")
  await expect(panel).toContainText('30 g / 0.85 yield x $0.002 per g = $0.07')
  await expect(panel).toContainText('4 of 4 recipe lines priced, from 1 invoice')
  await expect(panel.getByRole('link', { name: 'Edit recipe' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expectWhyPanel(page, burger.getByRole('button', { name: '20.3%' }))
  for (const tile of await page.getByTestId('stat-tile').getByRole('button').all()) await expectWhyPanel(page, tile)

  // One chart, with the same figures as a table and a sentence for a screen reader.
  const chart = page.getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText('Wings has the highest food cost, 25.5%')
  await expect(chart).toContainText('Target: 30.0%')
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row', { name: /Wings 25.5% 30.0%/ })).toBeVisible()
  await chart.getByRole('button', { name: 'View as chart' }).click()
  await page.getByRole('tab', { name: 'Menu map' }).click()
  await expect(page.getByTestId('primary-chart')).toHaveCount(1)
  await expect(page.getByTestId('primary-chart')).toContainText('Median sold')
  await page.getByRole('tab', { name: 'Food cost' }).click()
  await expectAccessible(page)
})

test('the page says what it is waiting for: setup, loading, an error, stale data and an empty result', async ({ page }) => {
  const live = await (await page.request.get(LIVE)).json()

  // Setup: a checklist with a count and a button each, and no charts or numbers.
  await page.route('**/margin-labor/menu-costs', (route) =>
    route.fulfill({ json: { ...live, state: 'setup', dishes: [], header: null, tiles: null, setup: [{ key: 'invoices', done: true, count: 3 }, { key: 'recipes', done: false, count: 0 }, { key: 'costed', done: false, count: 0 }, { key: 'prices', done: false, count: 0 }] } }),
  )
  await page.goto('/#menu-costs')
  await expect(page.getByTestId('header-sentence')).toHaveText('1 of 4 setup steps are done.')
  await expect(page.getByText('1 of 4 done')).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open recipes' }).first()).toBeVisible()
  await expect(page.getByTestId('primary-chart')).toHaveCount(0)
  await expect(page.getByTestId('stat-tile')).toHaveCount(0)
  await expectAccessible(page)

  // Loading: the page's shape in grey, not a spinner on a blank page.
  await page.unroute('**/margin-labor/menu-costs')
  await page.route('**/margin-labor/menu-costs', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1200))
    await route.fulfill({ json: live })
  })
  await page.reload()
  await expect(page.getByTestId('skeleton')).toBeVisible()
  await expect(page.getByRole('heading', { level: 1, name: 'Menu costs' })).toBeVisible()
  await expect(page.getByTestId('header-sentence')).toBeVisible()

  // Error: what failed, what still works, and Retry.
  await page.unroute('**/margin-labor/menu-costs')
  let fail = true
  await page.route('**/margin-labor/menu-costs', (route) => (fail ? route.fulfill({ status: 500, json: { detail: 'boom' } }) : route.fulfill({ json: live })))
  await page.reload()
  await expect(page.getByRole('alert')).toHaveText(/Menu costs did not load\. The rest of FullHouse still works\./)
  fail = false
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(page.getByTestId('header-sentence')).toBeVisible()

  // Stale: a banner with the last update and the fix; the numbers stay on the page, greyed.
  await page.unroute('**/margin-labor/menu-costs')
  await page.route('**/margin-labor/menu-costs', (route) => route.fulfill({ json: { ...live, stale: true, last_sales_date: '2026-11-03' } }))
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: 'Sales were last imported Tue Nov 3. Import a newer export.' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open connections' })).toBeVisible()
  await expect(page.getByRole('row', { name: /Burger/ })).toContainText('20.3%')
  await expect(page.locator('main.ml-stale')).toHaveCount(1)

  // Empty: the fact, stated.
  await page.unroute('**/margin-labor/menu-costs')
  await page.reload()
  await expect(page.getByTestId('header-sentence')).toHaveText('No dishes are over your 30.0% food cost target.')
})

test('a dish page shows its cost over time, its lines, and takes a yield edit', async ({ page }) => {
  await page.goto('/#menu-costs')
  await page.getByRole('link', { name: 'House Salad' }).click()
  await expect(page).toHaveURL(/#menu-costs\/[0-9a-f]{32}$/)
  await expect(page.getByTestId('header-sentence')).toHaveText('House Salad costs $0.71 a plate, 7.9% of its $9.00 price.')
  await expectPageRules(page)
  await expect(page.getByTestId('primary-chart').getByRole('heading')).toHaveText(/House Salad cost per plate held steady for \d+ weeks/)
  await expect(page.getByTestId('ingredient-costs').getByRole('heading')).toHaveText('Lettuce is the largest cost, $0.67 a plate')
  await expectWhyPanel(page, page.getByTestId('stat-tile').getByRole('button').first())
  await expectAccessible(page)

  // Lettuce at 75% yield instead of 90%: 200 g bought for the plate, $0.80, and the cost follows at once.
  await page.getByRole('button', { name: 'Edit recipe' }).click()
  const editor = page.getByRole('dialog', { name: 'Edit recipe' })
  await editor.getByRole('group', { name: 'Lettuce' }).getByLabel('Yield, %').fill('75')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByTestId('header-sentence')).toHaveText('House Salad costs $0.84 a plate, 9.3% of its $9.00 price.')
  // A yield over 100% is refused before anything is sent.
  await page.getByRole('button', { name: 'Edit recipe' }).click()
  await editor.getByRole('group', { name: 'Lettuce' }).getByLabel('Yield, %').fill('140')
  await editor.getByRole('button', { name: 'Save' }).click()
  await expect(editor.getByRole('alert')).toHaveText('Lettuce: the quantity must be more than 0 and the yield from 1 to 100.')
  await page.keyboard.press('Escape')
  await page.getByRole('link', { name: 'Menu costs' }).first().click()
  await expect(page).toHaveURL(/#menu-costs$/)
})

test('flow 1: a dearer invoice arrives, the burger is recosted, a finding appears, and the reprice is approved', async ({ page }) => {
  await page.goto('/#menu-costs')
  await expect(page.getByRole('row', { name: /Burger/ })).toContainText('20.3%')

  // The invoice: ground beef at $22.00 a kilo, up from $12.00.
  const now = new Date()
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}` // the server's own date
  const started = Date.now()
  const saved = await page.request.post(`${API}/api/v1/prices/invoices`, { data: { supplier_name: 'Margin Farm', invoice_date: today, lines: [{ description: 'Ground beef', qty: 2, unit_price: 22 }] } })
  expect(saved.status()).toBe(201)

  // The worker recosts the dishes that use beef; the page shows it on the next load. The spec allows 60 seconds.
  await expect(async () => {
    const body = await (await page.request.get(LIVE)).json()
    expect(body.dishes.find((d: { name: string }) => d.name === 'Burger').plate_cost).toBe('3.9406')
  }).toPass({ timeout: 60_000 })
  expect(Date.now() - started).toBeLessThan(60_000)
  await page.reload()
  await expect(page.getByTestId('header-sentence')).toHaveText('1 dish is over your 30.0% food cost target. Cost: about $315 a week.')
  await expectPageRules(page)
  const burger = page.getByRole('row', { name: /Burger/ })
  await expect(burger).toContainText('$3.94')
  await expect(burger).toContainText('32.8%')
  await expect(burger).toContainText('Over target') // a word and an icon, not a colour
  await expect(burger).toContainText('up $1.50')
  await expect(page.locator('.ml-table tbody tr').first()).toContainText('Burger') // largest dollars a week first

  // From the digest card it is two clicks to the decision: Review price, then Approve.
  await page.goto('/#digest')
  const card = page.getByTestId('module-card-menu_costs')
  await expect(card).toContainText('1 dish is over your 30.0% food cost target.')
  await expect(card).toContainText('$315 a week')
  await card.getByRole('link', { name: 'Review price' }).click()
  const review = page.getByRole('dialog', { name: 'Burger' })
  await expect(review).toContainText('Raise the price to $13.00')
  await expect(review).toContainText('Burger costs $3.94 a plate, 32.8% of its $12.00 price.')
  await expect(review).toContainText('$13.00 brings food cost to 30.3%. The rise is held to 10% of the price.')
  await expect(review).toContainText('$315 a week')
  await expect(page.getByRole('row', { name: /Burger/ })).toBeVisible() // the list stays in view behind the panel
  await review.getByRole('button', { name: 'Why' }).click()
  await expect(page.getByRole('dialog', { name: 'How this was worked out' })).toContainText('(cost per plate now - cost per plate 28 days ago) x units sold in 28 days / 4')
  await page.getByRole('dialog', { name: 'How this was worked out' }).getByRole('button', { name: 'Close' }).click()

  // Dismiss, then Undo within ten seconds: nothing was sent.
  await review.getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Burger reprice dismissed.' })).toBeVisible()
  await page.getByRole('status').filter({ hasText: 'Burger reprice dismissed.' }).getByRole('button', { name: 'Undo' }).click()
  await expect(burger.getByRole('button', { name: 'Review price' })).toBeVisible()
  let actions = await (await page.request.get(`${API}/api/v1/agents/actions?status=pending`)).json()
  expect(actions.filter((a: { action_type: string }) => a.action_type === 'menu_reprice')).toHaveLength(1)

  // Approve, by keyboard, and let the ten seconds run out.
  await burger.getByRole('button', { name: 'Review price' }).focus()
  await page.keyboard.press('Enter')
  await review.getByRole('button', { name: 'Approve' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Burger reprice approved.' })).toBeVisible()
  await expect(burger).toContainText('Undo')
  await expect(async () => {
    actions = await (await page.request.get(`${API}/api/v1/agents/actions?status=approved`)).json()
    expect(actions.filter((a: { action_type: string }) => a.action_type === 'menu_reprice')).toHaveLength(1)
  }).toPass({ timeout: 20_000 })
  await expect(burger).toContainText('Approved')
  // FullHouse changed no price: the burger is still $12.00 until a person changes it in the POS.
  await page.reload()
  await expect(page.getByRole('row', { name: /Burger/ })).toContainText('$12.00')
  await expect(page.getByRole('row', { name: /Burger/ })).toContainText('Approved')
  await expectAccessible(page)

  // The dish page marks the week the beef price changed and names the largest cost.
  await page.getByRole('link', { name: 'Burger' }).click()
  await expect(page.getByTestId('header-sentence')).toHaveText('Burger costs $3.94 a plate, 32.8% of its $12.00 price.')
  await expect(page.getByTestId('ingredient-costs')).toContainText('Ground beef is the largest cost, $3.30 a plate')
  await expect(page.getByTestId('ingredient-costs')).toContainText('up $1.50 in 28 days')
  await page.getByTestId('primary-chart').getByRole('button', { name: 'View as table' }).click()
  await expect(page.getByTestId('primary-chart').getByRole('row').last()).toContainText('Ground beef')
})

test('staff can read costs but cannot review a price or edit a recipe', async ({ page }) => {
  await signIn(page, ML_STAFF)
  await page.goto('/#menu-costs')
  await expect(page.getByRole('row', { name: /Burger/ })).toContainText('32.8%')
  await expect(page.getByRole('button', { name: 'Review price' })).toHaveCount(0)
  const dishes = (await (await page.request.get(LIVE)).json()).dishes
  const put = await page.request.put(`${ML}/recipes/${dishes[0].recipe_id}`, { data: { lines: [] } })
  expect(put.status()).toBe(403)
  expect((await page.request.post(`${ML}/menu-costs/${dishes[0].recipe_id}/reprice`)).status()).toBe(403)
})

test('on a phone the list and the digest are one column with no sideways scroll', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/#menu-costs')
  await expect(page.getByTestId('header-sentence')).toBeVisible()
  await expectNoSidewaysScroll(page)
  expect(await page.locator('.ml-table thead th:visible').count()).toBeLessThanOrEqual(3)
  await page.goto('/#digest')
  await expect(page.getByTestId('module-card-menu_costs')).toBeVisible()
  await expectNoSidewaysScroll(page)
})
