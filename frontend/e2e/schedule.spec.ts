import { expect, test } from '@playwright/test'
import { expectAccessible, expectPageRules, ML, signIn } from './ml'

/**
 * Module D, the schedule, in the Labor test kitchen: nine people, the owner's rules confirmed, 14 days of notice
 * asked for. The spec's flow 2: a manager generates a schedule, edits one shift, locks it, regenerates, and publishes.
 */
const OWNER = { email: 'labor@example.com', password: 'ml-password' }
const STAFF = { email: 'labor-staff@example.com', password: 'ml-password' }
const WEEK = `${ML}/labor/weeks/next`

test.beforeEach(async ({ page }) => signIn(page, OWNER))

test('flow 2: generate a schedule, edit one shift, lock it, regenerate around it, and publish', async ({ page }) => {
  test.setTimeout(240_000)
  await page.goto('/#labor')
  const panel = page.getByTestId('schedule')
  await expect(panel).toContainText(/No schedule yet for the week of /)
  await panel.getByRole('button', { name: 'Generate' }).click()
  await expect(panel.getByText('Draft v1')).toBeVisible({ timeout: 90_000 })
  // The header now says what the draft costs against expected sales; the tiles say what is still open.
  await expect(page.getByTestId('header-sentence')).toHaveText(/^Draft for [A-Z][a-z]{2} \d+: \d+ hours, \$[\d,]+, \d+\.\d% of expected sales\.$/)
  await expectPageRules(page)
  const tiles = page.getByTestId('stat-tile')
  await expect(tiles.nth(0)).toContainText('Scheduled hours')
  await expect(tiles.nth(1)).toContainText('Labor %')
  await expect(tiles.nth(2)).toContainText('Open shifts')
  await expect(tiles.nth(2)).toContainText('half hours understaffed')
  // The grid: a row a person, a column a day, an Open shifts row on top, hours at the end.
  const grid = panel.locator('table.ml-grid')
  await expect(grid.getByRole('columnheader')).toHaveCount(9)
  await expect(grid.getByRole('row', { name: /Open shifts/ })).toBeVisible()
  await expect(grid.getByRole('row', { name: /Ana Rivera/ })).toBeVisible()
  await panel.getByRole('button', { name: 'Line cook', exact: true }).click()
  await expect(grid.getByRole('row', { name: /Ana Rivera/ })).toHaveCount(0) // the role filter
  await expect(grid.getByRole('row', { name: /Gil Moreno/ })).toBeVisible()
  await panel.getByRole('button', { name: 'All' }).click()

  // Edit one shift from its dialog, by keyboard: end it an hour earlier and lock it.
  const before = (await (await page.request.get(WEEK)).json()).schedule
  const target = before.shifts.find((s: { staff_id: string | null; role_key: string; end: number; start: number }) => s.staff_id && s.role_key === 'server' && s.end - s.start >= 300)
  const who = before.people.find((p: { id: string }) => p.id === target.staff_id).name
  const bar = grid.getByRole('row', { name: new RegExp(who) }).getByRole('button').first()
  await bar.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Edit shift' })
  await expect(dialog.getByLabel('Person')).not.toHaveValue('')
  const shiftId = await (async () => {
    const person = await dialog.getByLabel('Person').inputValue()
    const start = Number(await dialog.getByLabel('Starts').inputValue())
    return before.shifts.find((s: { staff_id: string; start: number }) => s.staff_id === person && s.start === start).id as string
  })()
  const end = Number(await dialog.getByLabel('Ends').inputValue())
  await dialog.getByLabel('Ends').selectOption(String(end - 60))
  await dialog.getByLabel(/Lock: keep this shift/).check()
  await dialog.getByRole('button', { name: 'Save' }).click()
  await expect(dialog).toBeHidden()
  await expect(grid.getByText('Locked').first()).toBeVisible()
  const edited = (await (await page.request.get(WEEK)).json()).schedule
  const kept = edited.shifts.find((s: { id: string }) => s.id === shiftId)
  expect(kept.end).toBe(end - 60)
  expect(kept.locked).toBe(true)
  expect(Number(edited.hours_total)).toBeLessThan(Number(before.hours_total)) // the header and tiles follow each edit

  // Regenerate: version 2, with the locked shift exactly as it was.
  await panel.getByRole('button', { name: 'Regenerate' }).click()
  await expect(panel.getByText('Draft v2')).toBeVisible({ timeout: 90_000 })
  const again = (await (await page.request.get(WEEK)).json()).schedule
  expect(again.shifts.find((s: { id: string }) => s.id === shiftId)).toEqual(kept)
  expect(again.violations).toEqual([])

  // The day view by the hour: a shift's end moves with the arrow keys, as it does by dragging its edge.
  await panel.getByRole('button', { name: 'Day', exact: true }).click()
  const timeline = panel.getByTestId('day-timeline')
  await expect(timeline).toBeVisible()
  const first = timeline.getByRole('button').first()
  const label = ((await first.textContent()) ?? '').trim()
  await first.focus()
  await page.keyboard.press('ArrowRight')
  await expect(timeline.getByRole('button').first()).not.toHaveText(label)
  await timeline.getByRole('button').first().focus()
  await page.keyboard.press('ArrowLeft')
  await expect(timeline.getByRole('button').first()).toHaveText(label)
  await panel.getByRole('button', { name: 'Week', exact: true }).click()
  await expectAccessible(page)

  // Publish: the week starts inside the 14 days of notice the owner asked for, so it warns first.
  await panel.getByRole('button', { name: 'Publish' }).click()
  const warn = page.getByRole('dialog', { name: 'Before you publish' })
  await expect(warn.getByTestId('publish-warnings')).toContainText(/The week starts in \d+ days\. Your rule asks for 14 days of notice\./)
  await expect(warn).toContainText('Publishing notifies nobody. Tell the team, or export the week.')
  await warn.getByRole('button', { name: 'Publish anyway' }).click()
  const undo = page.getByRole('status').filter({ hasText: 'Schedule published.' })
  await undo.getByRole('button', { name: 'Undo' }).click() // nothing was sent
  expect((await (await page.request.get(WEEK)).json()).schedule.status).toBe('draft')
  await panel.getByRole('button', { name: 'Publish' }).click()
  await warn.getByRole('button', { name: 'Publish anyway' }).click()
  await expect(panel.getByText('Published v2')).toBeVisible({ timeout: 45_000 })
  await expect(page.getByTestId('header-sentence')).toHaveText(/^Published for /)
  const published = (await (await page.request.get(WEEK)).json()).schedule
  expect(published.status).toBe('published')
  // The export says who works when and holds no wage.
  const csv = await (await page.request.get(`${ML}/labor/schedules/${published.id}/export.csv`)).text()
  expect(csv.split('\n')[0].trim()).toBe('Date,Day,Person,Role,Start,End,Break minutes,Kind,Note')
  expect(csv).toContain(who)
  expect(csv).not.toMatch(/\d\d\.\d\d\d\d|rate/i)
})

test('staff see the published week with hours only: no cost, no rate, and no way to change it', async ({ page }) => {
  await signIn(page, STAFF)
  await page.goto('/#labor')
  await expect(page.getByTestId('header-sentence')).toHaveText(/^Published for [A-Z][a-z]{2} \d+: \d+ hours scheduled, \d+ needed\.$/)
  const text = await page.locator('main').innerText()
  expect(text).not.toMatch(/\$/)
  expect(text).not.toContain('Labor %')
  const panel = page.getByTestId('schedule')
  await expect(panel.locator('table.ml-grid').getByRole('row', { name: /Ana Rivera/ })).toBeVisible()
  for (const name of ['Regenerate', 'Publish', 'Rules', 'Add shift']) await expect(panel.getByRole('button', { name })).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'Cost' })).toHaveCount(0)
  const week = await (await page.request.get(WEEK)).json()
  expect(JSON.stringify(week)).not.toContain('hourly_rate')
  expect(week.schedule.cost_total).toBeUndefined()
  expect((await page.request.get(`${ML}/labor/team`)).status()).toBe(403)
})

test('below 1024 pixels the schedule is a read-only list by day, and the Cost tab shows labor share by day', async ({ page }) => {
  await page.goto('/#labor')
  await page.getByRole('tab', { name: 'Cost' }).click()
  const chart = page.getByTestId('primary-chart')
  await expect(chart.getByRole('heading')).toHaveText(/^[A-Z][a-z]+day has the highest labor share, \d+\.\d% of expected sales$/)
  await chart.getByRole('button', { name: 'View as table' }).click()
  await expect(chart.getByRole('row')).toHaveCount(8)
  await page.setViewportSize({ width: 900, height: 900 })
  const panel = page.getByTestId('schedule')
  await expect(panel.getByRole('list', { name: 'Shifts by day' })).toBeVisible()
  await expect(panel.locator('table.ml-grid')).toHaveCount(0)
  await expect(panel.getByRole('list', { name: 'Shifts by day' })).toContainText(/\d+:\d\d [ap]m to \d+:\d\d [ap]m, Server: /)
})
