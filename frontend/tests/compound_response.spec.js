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
 *   1. Start backend: `cd backend && uvicorn app.main:app --reload`
 *   2. Start frontend: `cd frontend && npm run dev`
 *   3. In another terminal: `cd frontend && npm run test:e2e`
 *
 * A "why did I get a fee?" query triggers the two-phase compound path
 * when gpt-5 is the Planner model. If the Planner routes differently
 * (e.g., fast-path), this test will fail the assertion — which is
 * correct signal that the prompt or model changed.
 */
import { test, expect } from '@playwright/test'

const LOGIN_USERNAME = process.env.TEST_USER || 'aryash'

async function loginAndOpenChat(page) {
  await page.goto('/login')
  await page.fill('input[type="text"]', LOGIN_USERNAME)
  await page.click('button:has-text("Sign in")')
  await expect(page).toHaveURL(/\/chat/)
}

async function sendMessageAndWaitForReply(page, message) {
  await page.fill('textarea', message)
  await page.click('button[type="submit"]')
  // Wait for the assistant reply to stop streaming. A widget card appearing
  // OR the thinking indicator disappearing both count.
  await page.waitForFunction(
    () => !document.querySelector('[data-testid="thinking-indicator"]'),
    null,
    { timeout: 60_000 },
  )
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
