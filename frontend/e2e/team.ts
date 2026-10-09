import { type APIRequestContext, expect, request } from '@playwright/test'

const API = `http://127.0.0.1:${process.env.E2E_API_PORT ?? '8100'}`

// Seeded by backend/scripts/seed_e2e.py.
const OWNER = { email: 'e2e@example.com', password: 'e2e-password' }

/**
 * Joins the seeded restaurant as a member, the way its team does: the owner
 * makes an invite link and the newcomer accepts it. `browser` -- a page's or
 * context's `request` -- ends up signed in as the new member. (Signing up
 * instead would open a restaurant of their own.)
 */
export async function joinAsMember(
  browser: APIRequestContext,
  person: { name: string; email: string; password?: string },
): Promise<void> {
  const owner = await request.newContext()
  try {
    expect((await owner.post(`${API}/api/v1/auth/login`, { data: OWNER })).ok()).toBeTruthy()
    const invite = await owner.post(`${API}/api/v1/auth/invites`, { data: { role: 'member', email: person.email } })
    expect(invite.ok()).toBeTruthy()
    const { token } = await invite.json()
    const joined = await browser.post(`${API}/api/v1/auth/invite/${token}`, {
      data: { name: person.name, email: person.email, password: person.password ?? 'a-long-password' },
    })
    expect((await joined.json()).role).toBe('member')
  } finally {
    await owner.dispose()
  }
}
