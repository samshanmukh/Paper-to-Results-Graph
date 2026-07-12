/** @type {import('@playwright/test').PlaywrightTestConfig} */
const config = {
  testDir: "./e2e",
  timeout: 60_000,
  retries: 1,
  use: {
    baseURL: process.env.VERIGRAPH_BASE_URL || "http://127.0.0.1:8787",
    headless: true,
    trace: "on-first-retry",
  },
  webServer: process.env.VERIGRAPH_BASE_URL
    ? undefined
    : {
        command: "python3 -m http.server 8787 --directory static",
        port: 8787,
        reuseExistingServer: true,
        timeout: 30_000,
      },
};

module.exports = config;
