import { defineConfig, devices } from '@playwright/test'
import dotenv from 'dotenv'
dotenv.config({ path: '.env.test', override: false })

const useWebServer = process.env.PW_USE_WEBSERVER === '1'
const pwPort = process.env.PW_PORT || '3101'
const pwBaseUrl = process.env.PW_BASE_URL || `http://127.0.0.1:${pwPort}`

const localE2eEnv = {
  NEXT_PUBLIC_SUPABASE_URL: 'http://127.0.0.1:54321',
  SUPABASE_URL: 'http://127.0.0.1:54321',
  NEXT_PUBLIC_SUPABASE_ANON_KEY: 'e2e-dummy-anon-key',
  SUPABASE_SERVICE_ROLE_KEY: 'e2e-dummy-service-role-key',
}

for (const [key, value] of Object.entries(localE2eEnv)) {
  if (!process.env[key]) {
    process.env[key] = value
  }
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: 1,
  reporter: [['html', { outputFolder: 'e2e-report', open: 'never' }], ['list']],
  use: {
    baseURL: pwBaseUrl,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'off',
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: useWebServer
    ? {
        command: `npx next dev --webpack -H 127.0.0.1 -p ${pwPort}`,
        url: pwBaseUrl,
        reuseExistingServer: false,
        env: {
          ...process.env,
          ...localE2eEnv,
          GOOGLE_APPLICATION_CREDENTIALS: process.env.GOOGLE_APPLICATION_CREDENTIALS || '',
        },
        timeout: 120_000,
      }
    : undefined,
  timeout: 30_000,
  expect: { timeout: 8_000 },
})
