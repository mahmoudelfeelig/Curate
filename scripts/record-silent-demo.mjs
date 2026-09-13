import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import { chromium } from "playwright-core";

const projectRoot = path.resolve(import.meta.dirname, "..");
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5173";
const apiUrl = process.env.FEED_PASSPORT_API_URL || "http://127.0.0.1:8000";
const acknowledgement = process.env.FEED_PASSPORT_DEMO_RECORDING_ACK || "";
const pace = Number(process.env.FEED_PASSPORT_DEMO_PACE_SCALE || "1");
const localModelWaitMs = 310_000;
const runName = `curate-silent-demo-${new Date().toISOString().replaceAll(/[:.]/g, "-")}`;
const outputRoot = process.env.FEED_PASSPORT_DEMO_OUTPUT_DIR || path.join(
  os.tmpdir(),
  "curate-demo-capture",
);
const outputDirectory = path.join(path.resolve(outputRoot), runName);
const rawVideoPath = path.join(outputDirectory, "curate-silent-demo-raw.webm");
const videoPath = path.join(outputDirectory, "curate-silent-demo.webm");
const reportPath = path.join(outputDirectory, "report.json");

const vagueGoal = "I want less ragebait and more science-based pages.";
const exactGoal = "Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, 10% Naruto, 5% One Piece, and 5% perfumes.";
const beforeLinks = [
  "https://www.youtube.com/watch?v=b4RageBt001 | Breaking outrage clip with no sources or useful context.",
  "https://www.youtube.com/watch?v=b4DramaBt02 | Creator drama framed to keep people angry.",
  "https://www.instagram.com/p/curate-before-rumor/ | A rumor thread presented as settled fact.",
  "https://www.instagram.com/reel/curate-before-clickbait/ | Shocking claims and an urgent clickbait caption.",
  "https://www.youtube.com/watch?v=b4SpaceSc03 | A short astronomy explainer about Europa.",
  "https://www.instagram.com/p/curate-before-sketch/ | A quiet sketchbook study.",
].join("\n");
const afterLinks = [
  "https://www.youtube.com/watch?v=afSpaceSc01 | An astrophysicist explains new evidence about Europa.",
  "https://www.youtube.com/watch?v=afCodeSci02 | A calm programming lesson about graph traversal.",
  "https://www.instagram.com/p/curate-after-science/ | A research paper explained with links to its methods.",
  "https://www.instagram.com/p/curate-after-drawing/ | A step-by-step character drawing study.",
  "https://www.youtube.com/watch?v=afAnimeAn03 | A thoughtful Naruto and One Piece animation analysis.",
  "https://www.instagram.com/reel/curate-after-perfume/ | A fragrance chemistry guide with ingredient context.",
].join("\n");

function assertLoopbackOrigin(value, label) {
  const parsed = new URL(value);
  if (
    parsed.protocol !== "http:"
    || parsed.hostname !== "127.0.0.1"
    || !parsed.port
    || parsed.username
    || parsed.password
    || parsed.pathname !== "/"
    || parsed.search
    || parsed.hash
  ) {
    throw new Error(`${label} must be an exact http://127.0.0.1:<port> origin.`);
  }
  return parsed.origin;
}

if (acknowledgement !== "REVISE ONLY THE LOCAL DEMO PASSPORT") {
  throw new Error(
    "Recording applies one local Passport revision. Set FEED_PASSPORT_DEMO_RECORDING_ACK "
      + "to the exact documented acknowledgement.",
  );
}
if (!Number.isFinite(pace) || pace <= 0 || pace > 2) {
  throw new Error("FEED_PASSPORT_DEMO_PACE_SCALE must be greater than zero and at most two.");
}
const allowedOrigins = new Set([
  assertLoopbackOrigin(baseUrl, "Browser URL"),
  assertLoopbackOrigin(apiUrl, "API URL"),
  "http://127.0.0.1:8080",
]);

async function browserExecutable() {
  for (const candidate of [
    process.env.FEED_PASSPORT_BROWSER_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean)) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Try the next locally installed Chromium browser.
    }
  }
  throw new Error("No supported local Chromium browser was found.");
}

const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds * pace));
const expectOk = async (url) => {
  const response = await fetch(url, { signal: AbortSignal.timeout(3_000) });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
};

async function center(locator, milliseconds = 2_800) {
  await locator.first().scrollIntoViewIfNeeded();
  await pause(milliseconds);
}

function keptSegments(durationMs, cuts) {
  const normalizedCuts = cuts
    .map(({ start_ms: start, end_ms: end }) => ({
      start: Math.max(0, Math.min(durationMs, start)),
      end: Math.max(0, Math.min(durationMs, end)),
    }))
    .filter(({ start, end }) => end - start >= 250)
    .sort((left, right) => left.start - right.start);
  const merged = [];
  for (const cut of normalizedCuts) {
    const previous = merged.at(-1);
    if (previous && cut.start <= previous.end) previous.end = Math.max(previous.end, cut.end);
    else merged.push({ ...cut });
  }
  const segments = [];
  let cursor = 0;
  for (const cut of merged) {
    if (cut.start > cursor) segments.push({ start: cursor, end: cut.start });
    cursor = Math.max(cursor, cut.end);
  }
  if (cursor < durationMs) segments.push({ start: cursor, end: durationMs });
  return segments.filter(({ start, end }) => end - start >= 250);
}

async function condenseWaitingTime(executablePath, sourcePath, destinationPath, cuts) {
  const editBrowser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--autoplay-policy=no-user-gesture-required", "--disable-background-networking"],
  });
  const editContext = await editBrowser.newContext({ acceptDownloads: true });
  const editPage = await editContext.newPage();
  await editPage.setContent(`
    <input id="source" type="file" accept="video/webm">
    <video id="video" muted playsinline></video>
    <canvas id="canvas" width="1440" height="900"></canvas>
  `);
  await editPage.locator("#source").setInputFiles(sourcePath);
  const metadata = await editPage.evaluate(async () => {
    const video = document.querySelector("#video");
    video.src = URL.createObjectURL(document.querySelector("#source").files[0]);
    if (video.readyState < 1) {
      await new Promise((resolve) => video.addEventListener("loadedmetadata", resolve, { once: true }));
    }
    if (video.readyState < 2) {
      await new Promise((resolve) => video.addEventListener("loadeddata", resolve, { once: true }));
    }
    return { durationMs: Math.round(video.duration * 1000) };
  });
  const segments = keptSegments(metadata.durationMs, cuts);
  if (!segments.length) throw new Error("The wait-condensing edit removed the entire recording.");

  const downloadPromise = editPage.waitForEvent("download", { timeout: 600_000 });
  const encoding = editPage.evaluate(async ({ requestedSegments }) => {
    const video = document.querySelector("#video");
    const canvas = document.querySelector("#canvas");
    const drawing = canvas.getContext("2d", { alpha: false });
    const stream = canvas.captureStream(30);
    const preferredTypes = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
    const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type));
    if (!mimeType) throw new Error("This browser cannot encode a WebM canvas recording.");
    const chunks = [];
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 5_500_000 });
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) chunks.push(event.data);
    });
    const stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve, { once: true }));
    recorder.start(1_000);
    for (const segment of requestedSegments) {
      const targetTime = segment.start / 1000;
      if (Math.abs(video.currentTime - targetTime) > 0.02) {
        video.currentTime = targetTime;
        await new Promise((resolve) => video.addEventListener("seeked", resolve, { once: true }));
      }
      drawing.drawImage(video, 0, 0, canvas.width, canvas.height);
      await video.play();
      await new Promise((resolve) => {
        const timer = setInterval(() => {
          drawing.drawImage(video, 0, 0, canvas.width, canvas.height);
          if (video.currentTime * 1000 >= segment.end || video.ended) {
            clearInterval(timer);
            video.pause();
            resolve();
          }
        }, 1000 / 30);
      });
    }
    recorder.stop();
    await stopped;
    const blob = new Blob(chunks, { type: mimeType });
    const anchor = document.createElement("a");
    anchor.href = URL.createObjectURL(blob);
    anchor.download = "curate-silent-demo.webm";
    anchor.click();
    return {
      mimeType,
      outputDurationMs: requestedSegments.reduce((sum, segment) => sum + segment.end - segment.start, 0),
    };
  }, { requestedSegments: segments });
  const [download, result] = await Promise.all([downloadPromise, encoding]);
  await download.saveAs(destinationPath);
  await editPage.locator("#source").setInputFiles(destinationPath);
  const outputMetadata = await editPage.evaluate(async () => {
    const video = document.querySelector("#video");
    const loaded = new Promise((resolve, reject) => {
      video.addEventListener("loadedmetadata", resolve, { once: true });
      video.addEventListener("error", () => reject(new Error("The condensed WebM could not be decoded.")), { once: true });
    });
    video.src = URL.createObjectURL(document.querySelector("#source").files[0]);
    await loaded;
    return {
      durationMs: Math.round(video.duration * 1000),
      width: video.videoWidth,
      height: video.videoHeight,
    };
  });
  await editContext.close();
  await editBrowser.close();
  return {
    ...result,
    plannedDurationMs: result.outputDurationMs,
    outputDurationMs: outputMetadata.durationMs,
    outputWidth: outputMetadata.width,
    outputHeight: outputMetadata.height,
    sourceDurationMs: metadata.durationMs,
    removedDurationMs: metadata.durationMs - result.outputDurationMs,
    segments,
  };
}

await fs.mkdir(outputDirectory, { recursive: true });
const health = await expectOk(`${apiUrl}/health`);
const model = await expectOk(`${apiUrl}/api/agent/model/status`);
if (health.status !== "healthy" || model.readiness !== "ready" || model.endpoint_scope !== "loopback_only") {
  throw new Error("The local API and verified loopback model must both be ready before recording.");
}
if (model.external_model_calls !== false || model.paid_model_calls !== false) {
  throw new Error("The silent local capture refuses an external or paid model provider.");
}

const externalRequests = [];
const consoleErrors = [];
const pageErrors = [];
const condensedWaits = [];
const visibleEvidence = {
  starting_feed_cards: 0,
  curated_feed_cards: 0,
  vague_goal_shown: false,
  exact_goal_shown: false,
};
let missionProof = {
  planned_with_local_model: false,
  executed_on_local_twin: false,
  rollback_state_verified: false,
};
const executablePath = await browserExecutable();
const browser = await chromium.launch({
  executablePath,
  headless: true,
  args: ["--disable-background-networking", "--disable-component-update"],
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: outputDirectory, size: { width: 1440, height: 900 } },
  serviceWorkers: "block",
});
await context.route("**/*", async (route) => {
  const url = route.request().url();
  const parsed = new URL(url);
  if (allowedOrigins.has(parsed.origin) || ["blob:", "data:"].includes(parsed.protocol)) {
    await route.continue();
  } else {
    externalRequests.push(url);
    await route.abort("blockedbyclient");
  }
});

const page = await context.newPage();
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => pageErrors.push(error.message));
const video = page.video();
const recordingStartedAt = Date.now();
const elapsed = () => Date.now() - recordingStartedAt;

async function openDesk(section, settleMs = 1_600) {
  await page.locator(`.desk-tabs button[data-section="${section}"]`).click();
  await page.locator(".workspace-stage").waitFor({ state: "visible" });
  await pause(settleMs);
}

async function waitWithoutRecording(label, action, completion) {
  await action();
  await pause(1_500);
  const startMs = elapsed();
  await completion();
  const endMs = elapsed();
  if (endMs - startMs >= 1_000) condensedWaits.push({ label, start_ms: startMs, end_ms: endMs });
  await pause(1_000);
}

try {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator(".desk-tabs").waitFor({ state: "visible", timeout: 30_000 });
  await pause(3_000);
  await openDesk("evidence");

  const textboxes = page.getByRole("textbox");
  await textboxes.nth(0).fill(vagueGoal);
  await textboxes.nth(1).fill(beforeLinks);
  visibleEvidence.vague_goal_shown = (await textboxes.nth(0).inputValue()) === vagueGoal;
  await pause(3_000);
  await page.getByTestId("evidence-capture").click();
  await page.getByTestId("feed-before").waitFor({ timeout: 15_000 });
  await center(page.getByTestId("feed-before"), 4_500);
  visibleEvidence.starting_feed_cards = await page.getByTestId("feed-before").locator("article").count();
  await page.getByTestId("evidence-apply").click();
  await page.locator('.curate-result[data-proposal-status="applied_to_passport"]').waitFor({ timeout: 15_000 });
  await pause(2_500);

  const previousProposalId = await page.locator(".curate-result").getAttribute("data-proposal-id");
  await textboxes.nth(0).fill(exactGoal);
  visibleEvidence.exact_goal_shown = (await textboxes.nth(0).inputValue()) === exactGoal;
  await page.getByTestId("evidence-capture").click();
  await page.waitForFunction(
    (priorId) => document.querySelector(".curate-result")?.dataset.proposalId !== priorId,
    previousProposalId,
    { timeout: 15_000 },
  );
  await page.locator(".target-stamp-grid").waitFor({ timeout: 15_000 });
  await center(page.locator(".target-stamp-grid"), 5_000);
  await page.getByTestId("evidence-apply").click();
  await page.locator('.curate-result[data-proposal-status="applied_to_passport"]').waitFor({ timeout: 15_000 });
  await pause(2_500);

  await openDesk("agent");
  const missionGoal = page.getByRole("textbox", { name: /outcome for the agent|what should curate change/i });
  await missionGoal.fill(exactGoal);
  await pause(4_000);
  await waitWithoutRecording(
    "local model planning",
    () => page.getByTestId("mission-model-plan").click(),
    () => page.locator('.mission-ledger[data-mission-status="awaiting_approval"]').waitFor({
      timeout: localModelWaitMs,
    }),
  );
  await center(page.locator(".mission-docket"), 3_500);
  const plannerDetails = page.locator(".model-planner-evidence");
  await plannerDetails.waitFor();
  await plannerDetails.locator("summary").click();
  await center(plannerDetails, 3_500);
  missionProof.planned_with_local_model = true;
  await page.getByTestId("mission-run").click();
  await page.locator(
    '.mission-ledger[data-mission-status="completed"], '
      + '.mission-ledger[data-mission-status="needs_human"]',
  ).waitFor({ timeout: 30_000 });
  missionProof.executed_on_local_twin = true;
  await center(page.locator(".mission-comparison"), 5_500);
  await page.getByTestId("mission-rollback").click();
  await page.locator('.mission-ledger[data-mission-status="rolled_back"]').waitFor({
    timeout: 30_000,
  });
  await page.getByText(/STATE VERIFIED|STARTING STATE RESTORED|START RESTORED/i).waitFor();
  missionProof.rollback_state_verified = true;
  await pause(3_500);
  await openDesk("evidence");

  const updatedTextboxes = page.getByRole("textbox");
  await updatedTextboxes.nth(1).fill(afterLinks);
  await pause(2_500);
  await page.getByTestId("evidence-compare").click();
  await page.getByTestId("feed-after").waitFor({ timeout: 15_000 });
  await center(page.getByTestId("feed-after"), 7_000);
  visibleEvidence.curated_feed_cards = await page.getByTestId("feed-after").locator("article").count();
  await page.getByRole("button", { name: "Open passport overview" }).click();
  await pause(4_500);
} finally {
  await page.close();
  await video.saveAs(rawVideoPath);
  await video.delete();
  await context.close();
  await browser.close();
}

const edit = await condenseWaitingTime(executablePath, rawVideoPath, videoPath, condensedWaits);
const videoBytes = await fs.readFile(videoPath);
const report = {
  schema: "curate/silent-demo-capture/v2",
  generated_at: new Date().toISOString(),
  video: videoPath,
  raw_video: rawVideoPath,
  sha256: createHash("sha256").update(videoBytes).digest("hex"),
  bytes: videoBytes.length,
  elapsed_ms: Date.now() - recordingStartedAt,
  presentation_duration_ms: edit.outputDurationMs,
  browser_origin: baseUrl,
  api_origin: apiUrl,
  model: {
    provider: model.provider,
    model_id: model.model_id,
    endpoint_scope: model.endpoint_scope,
    external_model_calls: model.external_model_calls,
    paid_model_calls: model.paid_model_calls,
  },
  goals_demonstrated: [
    { kind: "vague", text: vagueGoal },
    { kind: "exact_100_percent_mix", text: exactGoal },
  ],
  visible_evidence: visibleEvidence,
  local_passport_revised: true,
  local_mission: missionProof,
  edit: {
    method: "browser_canvas_media_recorder",
    disclosure: "Only inactive local-model wait time was removed; no result or interaction was synthesized.",
    raw_duration_ms: edit.sourceDurationMs,
    removed_wait_ms: edit.removedDurationMs,
    condensed_waits: condensedWaits,
    kept_segments: edit.segments,
    mime_type: edit.mimeType,
    encoded_width: edit.outputWidth,
    encoded_height: edit.outputHeight,
    expected_duration_ms: edit.plannedDurationMs,
  },
  social_account_accessed: false,
  social_action_executed: false,
  external_requests: externalRequests,
  console_errors: consoleErrors,
  page_errors: pageErrors,
  passed: visibleEvidence.vague_goal_shown
    && visibleEvidence.exact_goal_shown
    && visibleEvidence.starting_feed_cards >= 3
    && visibleEvidence.curated_feed_cards >= 3
    && missionProof.planned_with_local_model
    && missionProof.executed_on_local_twin
    && missionProof.rollback_state_verified
    && edit.outputDurationMs < 180_000
    && edit.outputWidth === 1440
    && edit.outputHeight === 900
    && externalRequests.length === 0
    && consoleErrors.length === 0
    && pageErrors.length === 0,
};
await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
if (!report.passed) throw new Error(`Silent demo capture failed; inspect ${reportPath}`);
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
