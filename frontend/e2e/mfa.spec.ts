import { createHmac } from 'node:crypto'
import { expect, test } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

/** RFC 6238, as an authenticator app computes it: the code for one 30-second step. */
function totp(secret: string, step = Math.floor(Date.now() / 30_000)): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of secret.toUpperCase()) bits += alphabet.indexOf(char).toString(2).padStart(5, '0')
  const key = Buffer.from(bits.match(/.{8}/g)!.map((byte) => parseInt(byte, 2)))
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(step))
  const digest = createHmac('sha1', key).update(counter).digest()
  const offset = digest[digest.length - 1] & 0x0f
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, '0')
}

test('turning on two-step sign-in, then signing in with a code', async ({ page }) => {
  const email = `tess-${Date.now()}@example.com`
  const password = 'a-long-password'
  expect((await page.request.post(`${API}/api/v1/auth/signup`, { data: { name: 'Tess', email, password } })).ok()).toBeTruthy()

  // --- set it up from the account tab
  await page.goto('/#profile/account')
  await page.getByRole('button', { name: 'Turn on two-step sign-in' }).click()
  await expect(page.getByRole('img', { name: 'QR code for your authenticator app' })).toBeVisible()
  await page.getByText('Can’t scan it?').click()
  const secret = (await page.getByLabel('Setup key').innerText()).trim()
  await page.getByLabel('6-digit code').fill(totp(secret))
  await page.getByRole('button', { name: 'Turn on two-step sign-in' }).click()

  const codes = page.getByRole('list', { name: 'Backup codes' }).getByRole('listitem')
  await expect(codes).toHaveCount(10)
  const continueButton = page.getByRole('button', { name: 'Continue' })
  await expect(continueButton).toBeDisabled()
  await page.getByLabel('I’ve saved these codes').check()
  await continueButton.click()
  await expect(page.getByText('On. 10 backup codes left.')).toBeVisible()

  // --- sign out, and back in: the password alone isn't enough
  await page.getByRole('button', { name: 'Sign out', exact: true }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Two-step sign-in' })).toBeVisible()

  await page.getByLabel('Code').fill('000000')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText("didn't work")

  // The next step's code: the one used to turn it on can't be used again.
  await page.getByLabel('Code').fill(totp(secret, Math.floor(Date.now() / 30_000) + 1))
  await page.getByRole('button', { name: 'Sign in' }).click()
  // Signed in: back on the account tab, which now shows two-step as on.
  await expect(page.getByText('On. 10 backup codes left.')).toBeVisible()
  await expect(page.getByLabel('Code', { exact: true })).toHaveCount(0)
})
