import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";

const API = "http://localhost:8765";
const tmp = process.env.E2E_TMP ?? "/tmp/news-clipper-e2e";

/** A 12-second recording: shots of different brightness with a tone (scene cuts are found). */
function syntheticRecording(): string {
  mkdirSync(`${tmp}/media`, { recursive: true });
  const out = `${tmp}/media/CT_V0001_20261104_1930.mp4`;
  const shots = ["navy", "darkred", "darkgreen", "gray"];
  const args = ["-hide_banner", "-loglevel", "error", "-y"];
  for (const c of shots) args.push("-f", "lavfi", "-i", `color=c=${c}:s=320x240:r=25:d=3`, "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3");
  const pads = shots.map((_, i) => `[${2 * i}:v][${2 * i + 1}:a]`).join("");
  args.push("-filter_complex", `${pads}concat=n=${shots.length}:v=1:a=1[v][a]`, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", out);
  execFileSync("ffmpeg", args);
  return out;
}

test("upload a recording, set a clip's range, fill in the review and approve it", async ({ page, request }) => {
  const file = syntheticRecording();
  await request.post(`${API}/api/programs`, { data: { name: "뉴스데스크" } });
  await page.addInitScript(() => localStorage.setItem("news-clipper.reviewer", "E2E 검수자"));

  // Upload (tus) and create the job; the whole pipeline runs in the request (Celery eager).
  await page.goto("/upload");
  await page.locator('input[type="file"]').setInputFiles(file);
  await page.locator('input[type="date"]').fill("2026-11-04");
  await page.getByRole("button", { name: "업로드 시작" }).click();
  await page.waitForURL(/\/jobs\/\d+$/, { timeout: 120_000 });
  const jobId = Number(page.url().split("/").pop());
  await expect(page.getByText("검수 대기").first()).toBeVisible({ timeout: 60_000 });

  // The fake model hears nothing: every clip needs a manual range. Open the first one.
  const clips = await (await request.get(`${API}/api/jobs/${jobId}/clips`)).json();
  expect(clips.length).toBeGreaterThan(0);
  const clip = clips[0];
  expect(clip.needs_manual_range).toBe(true);
  await page.goto(`/jobs/${jobId}/clips/${clip.id}`);
  await expect(page.getByText("구간을 직접 정해 주세요")).toBeVisible();

  // Keyboard: 1 s in, I (start), 4 s further, O (end).
  await page.locator(".editor-video").evaluate((v: HTMLVideoElement) => v.readyState >= 1 || new Promise((r) => v.addEventListener("loadedmetadata", r, { once: true })));
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("i");
  await expect(page.locator(".editor-transport")).not.toContainText("IN —", { timeout: 30_000 });
  for (let i = 0; i < 4; i++) await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("o");
  await expect
    .poll(async () => {
      const c = await (await request.get(`${API}/api/clips/${clip.id}`)).json();
      return c.end_sec - c.start_sec;
    })
    .toBeCloseTo(4, 1);

  // Metadata and thumbnails were made for the new range; edit the title and save (Ctrl+S).
  const title = page.locator(".meta-form input").first();
  await expect(title).toBeVisible({ timeout: 30_000 });
  await title.fill("E2E 검수 제목[목포MBC 뉴스데스크]");
  await page.keyboard.press("Control+s");
  await expect(page.getByRole("button", { name: "저장됨" })).toBeVisible();
  await expect(page.locator(".thumb-grid .thumb")).not.toHaveCount(0);

  // Acknowledge the warnings (no automatic range), then approve with Ctrl+Enter.
  const ack = page.getByLabel("경고를 확인했습니다");
  await ack.click(); // saved on the server first, then shown checked
  await expect(ack).toBeChecked();
  await expect(page.getByRole("button", { name: /승인/ })).toBeEnabled();
  await page.locator(".editor-head h1").click();
  await page.keyboard.press("Control+Enter");
  await expect
    .poll(async () => (await (await request.get(`${API}/api/clips/${clip.id}`)).json()).status)
    .toBe("APPROVED");
  const detail = await (await request.get(`${API}/api/clips/${clip.id}`)).json();
  expect(detail.metadata.title).toBe("E2E 검수 제목[목포MBC 뉴스데스크]");
  const audit = await (await request.get(`${API}/api/clips/${clip.id}/audit`)).json();
  expect(audit.map((a: { action: string }) => a.action)).toEqual(
    expect.arrayContaining(["range", "metadata", "acknowledge", "approve"]),
  );
  expect(audit[0].user_name).toBe("E2E 검수자");
});
