// Renders docs/social-preview.png (1280x640) for GitHub's social preview.
// Upload it under the repository's Settings -> General -> Social preview.
//
//   node scripts/capture-social-preview.mjs      (from frontend/, after capture-media.mjs)

import { chromium } from '@playwright/test'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 640 } })
await page.goto(pathToFileURL(join(here, 'social-preview.html')).href)
await page.waitForLoadState('networkidle')
await page.evaluate(() => document.fonts.ready)
const out = resolve(here, '..', '..', 'docs', 'social-preview.png')
await page.screenshot({ path: out })
await browser.close()
console.log(`wrote ${out}`)
