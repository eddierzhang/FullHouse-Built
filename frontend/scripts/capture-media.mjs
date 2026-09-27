// Records the README's demo GIF and screenshots from a running demo.
//
//   python dev.py demo                      # in one terminal
//   node scripts/capture-media.mjs          # in another, from frontend/
//
// Needs a freshly started demo (the GIF approves one of its proposals) and
// ffmpeg on PATH to turn the recording into a GIF. Writes to docs/.

import { chromium } from '@playwright/test'
import { spawnSync } from 'node:child_process'
import { mkdirSync, readdirSync, renameSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const BASE = process.env.DEMO_URL ?? 'http://localhost:8080'
const docs = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'docs')
const shots = join(docs, 'screenshots')
const videoDir = join(docs, '.video')
mkdirSync(shots, { recursive: true })
rmSync(videoDir, { recursive: true, force: true })

const VIEWPORT = { width: 1280, height: 800 }
const browser = await chromium.launch()

// ---- The GIF: ask Maestro, watch it delegate, approve what it found. ----
{
  const context = await browser.newContext({ viewport: VIEWPORT, recordVideo: { dir: videoDir, size: VIEWPORT } })
  const page = await context.newPage()
  await page.goto(`${BASE}/#overview`)
  await page.getByText('Live demo').waitFor()
  await page.waitForTimeout(1800)

  await page.getByRole('button', { name: 'New task' }).click()
  const dialog = page.getByRole('dialog', { name: 'New agent task' })
  await dialog.getByLabel('Agent').selectOption({ label: 'Maestro' })
  await dialog.getByLabel('What should it do?').pressSequentially('How is stock looking, and any gaps in the staff schedule?', { delay: 18 })
  await page.waitForTimeout(500)
  await dialog.getByRole('button', { name: 'Start task' }).click()

  await page.getByRole('heading', { level: 1, name: 'Runs' }).waitFor()
  await page.getByText('Summary', { exact: true }).waitFor({ timeout: 30_000 })
  await page.waitForTimeout(2500)

  await page.getByRole('link', { name: /Approvals/ }).click()
  await page.waitForTimeout(1500)
  const reorder = page.getByRole('listitem').filter({ hasText: 'Tomatoes' }).first()
  await reorder.getByRole('button', { name: 'Approve' }).hover()
  await page.waitForTimeout(600)
  await reorder.getByRole('button', { name: 'Approve' }).click()
  await page.getByText('Approved', { exact: true }).waitFor()
  await page.waitForTimeout(2000)

  await page.getByRole('link', { name: /Inventory/ }).click()
  await page.waitForTimeout(2500)
  await context.close()

  const [recording] = readdirSync(videoDir)
  const webm = join(videoDir, recording)
  const gif = join(docs, 'demo.gif')
  const ffmpeg = spawnSync('ffmpeg', [
    '-y', '-loglevel', 'error', '-ss', '0.8', '-i', webm,
    '-vf', 'fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle',
    '-loop', '0', gif,
  ], { stdio: 'inherit' })
  if (ffmpeg.error || ffmpeg.status !== 0) {
    renameSync(webm, join(docs, 'demo.webm'))
    console.log('ffmpeg unavailable or failed; kept docs/demo.webm instead')
  } else {
    console.log('wrote docs/demo.gif')
  }
  rmSync(videoDir, { recursive: true, force: true })
}

// ---- Screenshots, after the GIF has left a Maestro run to show. ----
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  const errors = []
  page.on('pageerror', (e) => errors.push(`${page.url()}: ${e.message}`))

  for (const route of ['overview', 'approvals', 'runs', 'agents', 'inventory', 'suppliers', 'menu', 'staff', 'profit']) {
    await page.goto(`${BASE}/#${route}`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(900)
    if (route === 'runs') {
      // The run list, not the banner's "a task for Maestro" button.
      await page.locator('.run-row').filter({ hasText: 'Maestro' }).first().click()
      await page.waitForTimeout(900)
    }
    await page.screenshot({ path: join(shots, `${route}.png`) })
  }

  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`${BASE}/#overview`)
  await page.waitForTimeout(1200)
  await page.screenshot({ path: join(shots, 'mobile.png') })

  console.log(errors.length ? `page errors:\n${errors.join('\n')}` : `wrote screenshots to ${shots}`)
}

await browser.close()
