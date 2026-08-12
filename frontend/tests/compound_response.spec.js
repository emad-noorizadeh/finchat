/**
 * P0.1 — prose-above-widget render order for compound messages.
 *
 * The compound-response flow has the Planner emit content (narration) +
 * `present_widget()` in the same turn-2 AIMessage. The chat consumer
 * appends a prose assistant message first, then the widget card as a
 * separate message in the chat list. This test asserts the DOM order
 * (prose bubble → widget card) holds in rendered output.
 *
 * Coverage:
 *   (a) First message in a session — compound shape renders correctly.
 *   (b) Mid-session — after prior turns, compound shape still renders
 *       correctly.
 *
 * Explicitly OUT of scope (per the compound-response plan):
 *   - Sub-agent interrupt resume (transfer/refund). Those widgets are
 *     terminal-by-design with no prose above them.
 *
 * Running:
 *   1. Start backend: `cd backend && python run.py`         (port 6000)
 *   2. Start frontend: `cd frontend && npm run dev`         (port 6001)
 *   3. In another terminal: `cd frontend && npm run test:e2e`
 *
 * A "why did I get a fee?" query triggers the two-phase compound path.
 * If the Planner routes differently (e.g., fast-path), this test will
 * fail the assertion — which is correct signal that the prompt or model
 * changed.
 */
import { test, expect } from '@playwright/test'

// Display name on the profile card, not a username — login is a
// profile-card picker (GET /profiles → click card → "Continue as …").
const PROFILE_NAME = process.env.TEST_PROFILE || 'Arya'

async function loginAndOpenChat(page) {
  await page.goto('/')
  // Persisted auth (zustand/persist in localStorage) redirects straight to
  // /chat; a fresh Playwright context lands on the profile picker instead.
  const alreadyIn = await page
    .waitForURL(/\/chat/, { timeout: 3_000 })
    .then(() => true)
    .catch(() => false)
  if (!alreadyIn) {
    await page.getByText(PROFILE_NAME, { exact: true }).first().click()
    await page.getByRole('button', { name: `Continue as ${PROFILE_NAME}` }).click()
    await expect(page).toHaveURL(/\/chat/, { timeout: 10_000 })
  }
}

async function sendMessageAndWaitForReply(page, message) {
  const before = await page.locator('[data-role="assistant"]').count()
  const input = page.getByPlaceholder('Type a message...')
  await input.fill(message)
  // The send button carries no type/name attributes — Enter submits
  // (Shift+Enter is newline).
  await input.press('Enter')

  // A new assistant message appearing marks the reply; then wait for the
  // stream to settle (no bouncing-dots placeholder, no tool spinners —
  // completed tool chips self-remove after ~1.5 s).
  await expect
    .poll(async () => page.locator('[data-role="assistant"]').count(), {
      timeout: 60_000,
    })
    .toBeGreaterThan(before)
  await page.waitForFunction(
    () => !document.querySelector('.animate-bounce'),
    null,
    { timeout: 60_000 },
  )
  await page.waitForTimeout(2_000)
}

test.describe('compound response — prose above widget', () => {
  test('first message — why-i-got-a-fee renders prose above widget', async ({ page }) => {
    await loginAndOpenChat(page)
    await sendMessageAndWaitForReply(page, 'why did I get a fee on my savings?')

    // Find all assistant message elements in order.
    const assistantMessages = page.locator('[data-role="assistant"]')
    const count = await assistantMessages.count()
    expect(count).toBeGreaterThanOrEqual(2)

    // Find the prose bubble and widget card indices.
    let proseIdx = -1
    let widgetIdx = -1
    for (let i = 0; i < count; i++) {
      const el = assistantMessages.nth(i)
      const messageType = await el.getAttribute('data-message-type')
      if (messageType === 'widget' && widgetIdx === -1) {
        widgetIdx = i
      } else if (messageType !== 'widget' && proseIdx === -1) {
        proseIdx = i
      }
    }

    expect(proseIdx).toBeGreaterThanOrEqual(0)
    expect(widgetIdx).toBeGreaterThan(proseIdx)
  })

  test('mid-session — compound shape still renders prose above widget', async ({ page }) => {
    await loginAndOpenChat(page)
    // First turn: simple one-widget query.
    await sendMessageAndWaitForReply(page, 'show my accounts')
    // Second turn: compound.
    await sendMessageAndWaitForReply(page, 'why was I charged a monthly fee?')

    const assistantMessages = page.locator('[data-role="assistant"]')
    const count = await assistantMessages.count()
    // At least: turn-1 widget, turn-2 prose, turn-2 widget.
    expect(count).toBeGreaterThanOrEqual(3)

    // Last two messages should be prose → widget in that order.
    const last = assistantMessages.nth(count - 1)
    const beforeLast = assistantMessages.nth(count - 2)
    expect(await beforeLast.getAttribute('data-message-type')).not.toBe('widget')
    expect(await last.getAttribute('data-message-type')).toBe('widget')
  })
})
