// End-to-end test (plan M5.8): upload → review → approve, with every external service faked.
// The backend runs in-process (SQLite, Celery eager, fake newsroom / alignment model / LLM).
//   npm run e2e            (needs ffmpeg and the backend virtualenv: cd backend && uv venv && uv pip install -e ".[dev]")
import { defineConfig } from "@playwright/test";

const API_PORT = 8765;
const WEB_PORT = 5174;
const tmp = process.env.E2E_TMP ?? "/tmp/news-clipper-e2e";

export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  retries: 0,
  use: { baseURL: `http://localhost:${WEB_PORT}`, viewport: { width: 1440, height: 1000 }, trace: "off" },
  webServer: [
    {
      command: "bash e2e/serve-backend.sh",
      url: `http://localhost:${API_PORT}/healthz`,
      env: { E2E_TMP: tmp, API_PORT: String(API_PORT) },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      env: { VITE_API_PROXY: `http://localhost:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
