/**
 * Playwright config for frontend E2E tests.
 *
 * Infrastructure bootstrap (one-time):
 *   cd frontend
 *   npm install --save-dev @playwright/test
 *   npx playwright install chromium
 *
 * Run:
 *   npm run test:e2e
 *
 * Assumes the backend is running at http://localhost:8000 and the Vite
 * dev server at http://localhost:6001. Start both before running.
 */
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  use: {
    baseURL: 'http://localhost:6001',
    trace: 'on-first-retry',
  },
  reporter: 'list',
})
