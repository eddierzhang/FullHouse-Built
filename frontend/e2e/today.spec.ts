import { expect, test } from '@playwright/test'
import { API, expectAccessible, expectNoSidewaysScroll, ML, signIn } from './ml'

/**
 * Module D, Today, in the Labor test kitchen. This week's schedule is published with four servers at dinner. The
 * spec's flow 3: during service, sales run below forecast; a cut suggestion appears; the manager accepts it.
 * The page is read as of 7:15 pm on the restaurant's clock, which the test backend allows.
 */
const OWNER = { email: 'labor@example.com', password: 'ml-password' }
const STAFF = { email: 'labor-staff@example.com', password: 'ml-password' }

/** Today's orders so far, at half the usual covers: lunch, and dinner up to 7:00 pm. */
function slowDay(today: string, busy: boolean): string {
  const rows = ['Order ID,Opened,Dining Option,Guests,SKU,Item,Qty,Net']
  const add = (name: string, hour: number, minute: number, count: number, covers: number) => {
    for (let n = 0; n < count; n++) {
      const at = hour * 60 + minute + 15 * n
      const time = `${String(Math.floor(at / 60)).padStart(2, '0')}:${String(at % 60).padStart(2, '0')}`
      rows.push(`T-${today}-${name}${n},${today} ${time},Dine In,${covers},BURGER,Burger,${covers},${covers * 20}`)
    }
  }
  add('L', 11, 30, 10, 2)
  add('D', 17, 0, 9, busy ? 4 : 3)
  return rows.join('\n')
}

test.beforeEach(async ({ page }) => signIn(page, OWNER))

test('before any orders are in, the page says so and makes no suggestion', async ({ page }) => {
  const first = await (await page.request.get(`${ML}/labor/standards`)).json() // the date only: reading Today itself makes suggestions for the real clock
  await page.goto(`/#labor/today?at=${first.today}T11:45:00`)
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(/^Today, [A-Z][a-z]{2} [A-Z][a-z]{2} \d+$/)
  await expect(page.getByTestId('header-sentence')).toHaveText(/^Too early to say\. 0 covers so far, \d+ expected\.$/)
  await expect(page.getByTestId('suggestions-off')).toHaveText('Suggestions start once 15% of the day has passed.')
  await expect(page.getByRole('article', { name: 'Suggestion' })).toHaveCount(0)
})

test('flow 3: sales run below forecast during service, a cut suggestion appears, and the manager accepts it', async ({ page }) => {
  test.setTimeout(120_000)
  const first = await (await page.request.get(`${ML}/labor/standards`)).json() // the date only: reading Today itself makes suggestions for the real clock
  const today: string = first.today
  const busy = [5, 6].includes(new Date(`${today}T12:00:00`).getDay())
  const imported = await (await page.request.post(`${ML}/data/import`, { data: { kind: 'orders', text: slowDay(today, busy) } })).json()
  expect(imported.created).toBe(19)
  const at = `${today}T19:15:00`

  await page.goto(`/#labor/today?at=${at}`)
  const so = busy ? 56 : 47
  await expect(page.getByTestId('header-sentence')).toHaveText(`Running 50.0% below forecast. ${so} covers so far, ${so * 2} expected.`)
  const sentence = (await page.getByTestId('header-sentence').innerText()).trim()
  expect(sentence.split(/\s+/).length).toBeLessThanOrEqual(20)
  expect(await page.getByTestId('stat-tile').count()).toBeLessThanOrEqual(3)
  await expect(page.getByTestId('primary-chart')).toHaveCount(1)
  await expect(page.locator('main table')).toHaveCount(0) // one column, no table

  // The suggestion: one sentence, the saving, why this person, when it expires, and two buttons.
  const card = page.getByRole('article', { name: 'Suggestion' })
  await expect(card).toContainText('Cut one server at 7:45 pm.')
  await expect(card).toContainText('Saves about $28.00.')
  await expect(card).toContainText('Cal O. has been on longest.')
  await expect(card).toContainText('Expires 7:35 pm')
  const tiles = page.getByTestId('stat-tile')
  await expect(tiles.nth(0)).toContainText(String(so))
  await expect(tiles.nth(0)).toContainText(`expected ${so * 2}`)
  await expect(tiles.nth(1)).toContainText('4 on')
  await expect(tiles.nth(1)).toContainText(/\d needed now, \d if busy/)
  await expect(tiles.nth(2)).toContainText('Labor % today')
  const chart = page.getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText(new RegExp(`^\\d+ covers are now expected today, against ${busy ? 168 : 136} this morning$`))
  await expect(chart).toContainText('Now')
  await expect(chart).toContainText('Revised')
  await expect(page.getByTestId('today-coverage').getByRole('heading')).toHaveText('Server: people on against people needed, to close')
  await card.getByRole('button', { name: 'Why' }).click()
  const panel = page.getByRole('dialog', { name: 'How this was worked out' })
  await expect(panel).toContainText('Multiplier = weight x pace + (1 - weight)')
  await expect(panel).toContainText('0.5000')
  await page.keyboard.press('Escape')
  await expectAccessible(page)
  // The manager is told by a notification too; nobody else is.
  expect(await (await page.request.get(`${API}/api/v1/notifications`)).text()).toContain('Cut one server at 7:45 pm.')

  // Accept, after an Undo: the decision is recorded and the schedule follows. FullHouse messages nobody.
  await card.getByRole('button', { name: 'Dismiss' }).click()
  await page.getByRole('status').filter({ hasText: 'Suggestion dismissed.' }).getByRole('button', { name: 'Undo' }).click()
  await expect(card).toBeVisible()
  await card.getByRole('button', { name: 'Accept' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('status').filter({ hasText: 'Suggestion accepted. Tell the person yourself.' })).toBeVisible()
  await expect(async () => {
    const body = await (await page.request.get(`${ML}/labor/today?at=${at}`)).json()
    expect(body.suggestion).toBeNull()
    expect(body.coverage.server.find((s: { label: string }) => s.label === '8:00').scheduled).toBe(3) // Cal is off from 7:45
  }).toPass({ timeout: 45_000 })
  await expect(card).toHaveCount(0)
})

test('staff see the pace and the people, with no suggestion, saving or labor cost', async ({ page }) => {
  await signIn(page, STAFF)
  const first = await (await page.request.get(`${ML}/labor/standards`)).json() // the date only: reading Today itself makes suggestions for the real clock
  await page.goto(`/#labor/today?at=${first.today}T19:15:00`)
  await expect(page.getByTestId('header-sentence')).toHaveText(/^Running 50\.0% below forecast\./)
  await expect(page.getByRole('article', { name: 'Suggestion' })).toHaveCount(0)
  const text = await page.locator('main').innerText()
  expect(text).not.toMatch(/\$/)
  expect(text).not.toContain('Labor %')
  const body = await (await page.request.get(`${ML}/labor/today?at=${first.today}T19:15:00`)).text()
  expect(body).not.toContain('savings_estimate')
  expect(body).not.toContain('labor_so_far')
  expect(await (await page.request.get(`${API}/api/v1/notifications`)).text()).not.toContain('Cut one server')
})

test('on a phone Today is one column with no sideways scroll, and the week page links to it', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  const first = await (await page.request.get(`${ML}/labor/standards`)).json() // the date only: reading Today itself makes suggestions for the real clock
  await page.goto(`/#labor/today?at=${first.today}T19:15:00`)
  await expect(page.getByTestId('header-sentence')).toBeVisible()
  await expectNoSidewaysScroll(page)
  const boxes = await page.getByTestId('stat-tile').evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().left)))
  expect(new Set(boxes).size).toBe(1) // the tiles stack: one column
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/#labor/week/' + first.today)
  await page.locator('main').getByRole('link', { name: 'Today' }).click()
  await expect(page).toHaveURL(/#labor\/today$/)
})
