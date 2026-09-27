import { defineConfig, devices } from '@playwright/test'

const API_PORT = process.env.E2E_API_PORT ?? '8100'
const WEB_PORT = process.env.E2E_WEB_PORT ?? '5174'
const API = `http://127.0.0.1:${API_PORT}`
const WEB = `http://127.0.0.1:${WEB_PORT}`

export default defineConfig({
  testDir: './e2e',
  // One shared, seeded backend: tests change its data, so run them in order.
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: WEB,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: [
    {
      command: 'node e2e/start-backend.mjs',
      url: `${API}/health`,
      env: { E2E_API_PORT: API_PORT, E2E_WEB_ORIGIN: WEB },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      // 127.0.0.1 explicitly: on Windows "localhost" can resolve to IPv6 only.
      command: `npx vite --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: WEB,
      env: { VITE_API_BASE: API },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})

