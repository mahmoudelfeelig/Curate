import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import { chromium } from "playwright-core";
import {
  loadPlatformFeedCaptureManifest,
  publicCaptureAttestation,
} from "./platform-feed-capture-evidence.mjs";
import {
  DEMO_RUNTIME_BOUNDS_MS,
  demoChapters,
  managedAwsProof,
  platformFeedStory,
  tutorialFeatures,
  validateDemoStoryboard,
} from "./demo-storyboard.mjs";

const projectRoot = path.resolve(import.meta.dirname, "..");
const execFileAsync = promisify(execFile);
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5173";
const apiUrl = process.env.FEED_PASSPORT_API_URL || "http://127.0.0.1:8000";
const acknowledgement = process.env.FEED_PASSPORT_DEMO_RECORDING_ACK || "";
const platformCaptureManifestPath = process.env.FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST || "";
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
const keyframeDirectory = path.join(outputDirectory, "keyframes");
const managedProofDirectory = path.resolve(
  process.env.FEED_PASSPORT_MANAGED_PROOF_DIR
    || path.join(projectRoot, "artifacts", "local", "judge-access"),
);

const vagueGoal = "I want less ragebait and more science-based pages.";
const exactGoal = "Make it 50% astronomy, 15% coding, 12% drawing, 3% anime, 10% Naruto, 5% One Piece, and 5% perfumes.";
const beforeLinks = [
  "https://www.youtube.com/watch?v=b4RageBt001",
  "https://www.youtube.com/watch?v=b4DramaBt02",
  "https://www.youtube.com/watch?v=b4SpaceSc03",
  "https://bsky.app/profile/curate.demo/post/3before01",
  "https://bsky.app/profile/curate.demo/post/3before02",
  "https://bsky.app/profile/curate.demo/post/3before03",
].join("\n");
const afterLinks = [
  "https://www.youtube.com/watch?v=afSpaceSc01",
  "https://www.youtube.com/watch?v=afCodeSci02",
  "https://www.youtube.com/watch?v=afAnimeAn03",
  "https://bsky.app/profile/curate.demo/post/3after001",
  "https://bsky.app/profile/curate.demo/post/3after002",
  "https://bsky.app/profile/curate.demo/post/3after003",
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

async function sourceAttestation() {
  const safeDirectory = projectRoot.replaceAll("\\", "/");
  const git = (...args) => execFileAsync("git", ["-c", `safe.directory=${safeDirectory}`, ...args], {
    cwd: projectRoot,
    encoding: "utf8",
    windowsHide: true,
  });
  const [{ stdout: revision }, { stdout: status }, manifestText] = await Promise.all([
    git("rev-parse", "HEAD"),
    git("status", "--short", "--untracked-files=no"),
    fs.readFile(path.join(projectRoot, "artifacts", "evidence", "manifest.json"), "utf8"),
  ]);
  const dirtyPaths = status.trim().split(/\r?\n/).filter(Boolean);
  if (dirtyPaths.length) {
    throw new Error(`The silent demo must be recorded from a clean commit; ${dirtyPaths.length} tracked path(s) are dirty.`);
  }
  const manifest = JSON.parse(manifestText);
  const evidenceSourceSha256 = manifest?.source?.snapshot_sha256;
  const evidenceSourceFiles = manifest?.source?.snapshot_file_count;
  if (!evidenceSourceSha256 || !Number.isInteger(evidenceSourceFiles)) {
    throw new Error("The evidence manifest is missing its source snapshot binding.");
  }
  return {
    git_revision: revision.trim(),
    git_clean: true,
    evidence_source_sha256: evidenceSourceSha256,
    evidence_source_files: evidenceSourceFiles,
  };
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
    let durationSeconds = video.duration;
    if (!Number.isFinite(durationSeconds)) {
      durationSeconds = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error("The condensed WebM duration could not be recovered.")), 10_000);
        video.addEventListener("timeupdate", () => {
          clearTimeout(timeout);
          resolve(Number.isFinite(video.duration) ? video.duration : video.currentTime);
        }, { once: true });
        video.currentTime = Number.MAX_SAFE_INTEGER;
      });
      video.currentTime = 0;
    }
    return {
      durationMs: Math.round(durationSeconds * 1000),
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
await fs.mkdir(keyframeDirectory, { recursive: true });
validateDemoStoryboard();
const source = await sourceAttestation();
const platformCaptureEvidence = await loadPlatformFeedCaptureManifest(platformCaptureManifestPath);
async function managedProofAttestation() {
  const [healthReceipt, planReceipt] = await Promise.all([
    fs.readFile(path.join(managedProofDirectory, "managed-health-receipt.json"), "utf8").then(JSON.parse),
    fs.readFile(path.join(managedProofDirectory, "managed-plan-feed-receipt.json"), "utf8").then(JSON.parse),
  ]);
  const valid = healthReceipt.schema === "curate/managed-agentcore-proof/v1"
    && healthReceipt.operation === "health"
    && healthReceipt.invocation_count === 1
    && healthReceipt.result?.status === "healthy"
    && planReceipt.schema === "curate/managed-agentcore-proof/v1"
    && planReceipt.operation === "plan_feed"
    && planReceipt.invocation_count === 1
    && planReceipt.result?.kind === "feed_goal_proposal"
    && planReceipt.result?.approved === false
    && planReceipt.result?.executed === false
    && planReceipt.result?.consent_created === false
    && planReceipt.result?.evidence?.explicit_percentages_enforced === true;
  if (!valid) throw new Error("The redacted managed AgentCore proof is incomplete or unsafe to present.");
  return {
    available: true,
    health: "healthy",
    plan_returned: true,
    exact_percentages_preserved: true,
    account_changes: false,
    current_source: healthReceipt.source_commit === source.git_revision
      && planReceipt.source_commit === source.git_revision,
  };
}
const managedProof = await managedProofAttestation();
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
  starting_youtube_cards: 0,
  starting_bluesky_cards: 0,
  curated_youtube_cards: 0,
  curated_bluesky_cards: 0,
  vague_goal_shown: false,
  exact_goal_shown: false,
  copy_feed_previewed: false,
  copy_feed_result_shown: false,
  incognito_issued: false,
  incognito_revoked: false,
  blend_page_shown: false,
  blend_invitation_created: false,
  blend_activated: false,
  blend_stopped: false,
  actual_platform_pages_before: 0,
  actual_platform_pages_after: 0,
  platform_scroll_sequences: 0,
  platform_comparisons: 0,
  feed_labels_shown: 0,
  tutorial_chapters_shown: [],
  tutorial_features_shown: [],
  managed_aws_receipt_shown: false,
  keyframes: [],
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

async function saveKeyframe(name) {
  const file = path.join(keyframeDirectory, `${name}.png`);
  await page.screenshot({ path: file });
  visibleEvidence.keyframes.push(path.basename(file));
}

async function showChapter(id, milliseconds = 2_800) {
  const chapter = demoChapters.find((item) => item.id === id);
  if (!chapter) throw new Error(`Unknown demo chapter ${id}.`);
  await page.evaluate((item) => {
    document.querySelector("#curate-demo-chapter")?.remove();
    const overlay = document.createElement("section");
    overlay.id = "curate-demo-chapter";
    overlay.innerHTML = `
      <style>
        #curate-demo-chapter { position: fixed; inset: 0; z-index: 100100; display: grid; place-items: center; background: radial-gradient(circle at 72% 26%, rgba(196,61,69,.28), transparent 34%), #10192a; color: #f7edda; font-family: Georgia, serif; }
        #curate-demo-chapter div { width: min(860px, 78vw); padding: 58px 64px; border: 1px solid rgba(247,237,218,.34); border-radius: 28px; background: rgba(20,34,55,.92); box-shadow: 0 28px 80px rgba(0,0,0,.35); }
        #curate-demo-chapter small { color: #ef9a9f; font: 800 15px/1 ui-monospace, monospace; letter-spacing: .12em; text-transform: uppercase; }
        #curate-demo-chapter h2 { margin: 18px 0 14px; font-size: clamp(44px, 5vw, 72px); line-height: .98; letter-spacing: -.035em; }
        #curate-demo-chapter p { margin: 0; max-width: 720px; color: #d8cdb8; font: 500 24px/1.45 system-ui, sans-serif; }
      </style>
      <div><small></small><h2></h2><p></p></div>`;
    overlay.querySelector("small").textContent = item.eyebrow;
    overlay.querySelector("h2").textContent = item.title;
    overlay.querySelector("p").textContent = item.detail;
    document.body.append(overlay);
  }, chapter);
  visibleEvidence.tutorial_chapters_shown.push(id);
  await pause(milliseconds);
  await page.evaluate(() => document.querySelector("#curate-demo-chapter")?.remove());
}

async function showGuide(title, detail, milliseconds = 2_200) {
  await page.evaluate(({ guideTitle, guideDetail }) => {
    document.querySelector("#curate-demo-guide")?.remove();
    const guide = document.createElement("aside");
    guide.id = "curate-demo-guide";
    guide.innerHTML = `<style>
      #curate-demo-guide { position: fixed; right: 28px; bottom: 28px; z-index: 100090; width: 390px; padding: 18px 20px; border: 1px solid rgba(247,237,218,.45); border-left: 7px solid #c43d45; border-radius: 16px; background: rgba(16,25,42,.94); color: #f7edda; box-shadow: 0 16px 40px rgba(0,0,0,.3); font-family: system-ui, sans-serif; }
      #curate-demo-guide b { display: block; font-size: 17px; }
      #curate-demo-guide span { display: block; margin-top: 5px; color: #d8cdb8; font-size: 14px; line-height: 1.35; }
    </style><b></b><span></span>`;
    guide.querySelector("b").textContent = guideTitle;
    guide.querySelector("span").textContent = guideDetail;
    document.body.append(guide);
  }, { guideTitle: title, guideDetail: detail });
  await pause(milliseconds);
  await page.evaluate(() => document.querySelector("#curate-demo-guide")?.remove());
}

async function showPlatformFeedCapture(capture, milliseconds = 9_000) {
  const scene = platformFeedStory[capture.platform]?.[capture.phase];
  if (!scene) throw new Error(`Missing storyboard for ${capture.platform}:${capture.phase}.`);
  const dataUrl = `data:${capture.mime_type};base64,${capture.bytes.toString("base64")}`;
  await page.evaluate(({ captureDataUrl, platform, phase, story }) => {
    document.querySelector("#platform-feed-capture")?.remove();
    const overlay = document.createElement("section");
    overlay.id = "platform-feed-capture";
    overlay.setAttribute("role", "img");
    overlay.setAttribute("aria-label", `${platform} dummy-account home feed, ${phase}, scrolling walkthrough`);
    const style = document.createElement("style");
    style.textContent = `
      #platform-feed-capture { position: fixed; inset: 0; z-index: 100000; overflow: hidden; background: #0b1020; color: #fff; font-family: system-ui, sans-serif; }
      #platform-feed-capture .feed-sheet { position: absolute; inset: 0 0 auto; transform: translateY(var(--from)); transition: transform 5.2s cubic-bezier(.22,.74,.24,1); }
      #platform-feed-capture.scrolling .feed-sheet { transform: translateY(var(--to)); }
      #platform-feed-capture img { display: block; width: 100%; height: auto; }
      #platform-feed-capture .platform-pill { position: fixed; z-index: 4; right: 24px; top: 22px; padding: 10px 15px; border: 1px solid rgba(255,255,255,.55); border-radius: 999px; background: rgba(11,16,32,.82); backdrop-filter: blur(12px); font: 800 15px/1 ui-monospace, monospace; letter-spacing: .06em; text-transform: uppercase; }
      #platform-feed-capture .goal { position: fixed; z-index: 4; left: 50%; bottom: 22px; transform: translateX(-50%); width: min(840px, calc(100vw - 56px)); padding: 13px 20px; border: 1px solid rgba(255,255,255,.42); border-radius: 16px; background: rgba(11,16,32,.88); box-shadow: 0 14px 40px rgba(0,0,0,.35); text-align: center; font-weight: 800; }
      #platform-feed-capture .label { position: absolute; z-index: 3; left: var(--x); top: var(--y); transform: translate(-50%, -50%); padding: 8px 12px; border: 2px solid currentColor; border-radius: 999px; background: rgba(11,16,32,.9); box-shadow: 0 8px 24px rgba(0,0,0,.35); font: 850 14px/1 system-ui, sans-serif; white-space: nowrap; }
      #platform-feed-capture .label::after { content: ""; position: absolute; left: 50%; top: 100%; width: 2px; height: 28px; background: currentColor; opacity: .8; }
      #platform-feed-capture .label[data-tone="down"] { color: #ff8a92; }
      #platform-feed-capture .label[data-tone="up"] { color: #8ee0b8; }
      #platform-feed-capture .label[data-tone="change"] { color: #ffd16b; }
      #platform-feed-capture .label[data-tone="neutral"] { color: #8ac7ff; }
    `;
    const sheet = document.createElement("div");
    sheet.className = "feed-sheet";
    sheet.style.setProperty("--from", `${story.scrollFrom}px`);
    sheet.style.setProperty("--to", `${story.scrollTo}px`);
    const image = document.createElement("img");
    image.alt = `${platform} home feed ${phase}`;
    image.src = captureDataUrl;
    sheet.append(image);
    for (const item of story.labels) {
      const label = document.createElement("span");
      label.className = "label";
      label.dataset.tone = item.tone;
      label.textContent = item.text;
      label.style.setProperty("--x", `${item.x}%`);
      label.style.setProperty("--y", `${item.y}%`);
      sheet.append(label);
    }
    const pill = document.createElement("div");
    pill.className = "platform-pill";
    pill.textContent = `${platform} · ${phase}`;
    const goal = document.createElement("div");
    goal.className = "goal";
    goal.textContent = `Goal: less ragebait · more trustworthy, useful content`;
    overlay.append(style, sheet, pill, goal);
    document.body.append(overlay);
    return image.decode();
  }, {
    captureDataUrl: dataUrl,
    platform: capture.platform === "youtube" ? "YouTube" : "Bluesky",
    phase: capture.phase.toUpperCase(),
    story: scene,
  });
  await pause(1_400);
  if (capture.platform === "youtube" && capture.phase === "before") await saveKeyframe("01-youtube-before-scroll");
  await page.evaluate(() => document.querySelector("#platform-feed-capture")?.classList.add("scrolling"));
  await pause(Math.max(4_500, milliseconds - 2_800));
  if (capture.platform === "youtube" && capture.phase === "after") await saveKeyframe("04-youtube-after-scroll");
  await pause(1_400);
  await page.evaluate(() => document.querySelector("#platform-feed-capture")?.remove());
  visibleEvidence.platform_scroll_sequences += 1;
  visibleEvidence.feed_labels_shown += scene.labels.length;
}

async function showCapturedFeeds(phase) {
  const captures = platformCaptureEvidence.captures.filter((capture) => capture.phase === phase);
  for (const capture of captures) await showPlatformFeedCapture(capture);
  visibleEvidence[`actual_platform_pages_${phase}`] = captures.length;
}

async function showPlatformComparison(platform, milliseconds = 5_500) {
  const captures = Object.fromEntries(platformCaptureEvidence.captures
    .filter((capture) => capture.platform === platform)
    .map((capture) => [capture.phase, `data:${capture.mime_type};base64,${capture.bytes.toString("base64")}`]));
  const copy = platform === "youtube"
    ? { title: "Ragebait recommendation", result: "shown → removed", detail: "The science and calm-listening choices stay in view." }
    : { title: "First recommendation", result: "changed", detail: "The selected post is gone and a different item moves up." };
  await page.evaluate(({ platformName, images, text }) => {
    const overlay = document.createElement("section");
    overlay.id = "curate-feed-comparison";
    overlay.innerHTML = `<style>
      #curate-feed-comparison { position: fixed; inset: 0; z-index: 100050; display: grid; grid-template-rows: auto 1fr auto; gap: 20px; padding: 26px; background: #10192a; color: #f7edda; font-family: system-ui, sans-serif; }
      #curate-feed-comparison header { display: flex; align-items: end; justify-content: space-between; gap: 24px; }
      #curate-feed-comparison small { color: #ef9a9f; font: 800 13px/1 ui-monospace, monospace; letter-spacing: .1em; text-transform: uppercase; }
      #curate-feed-comparison h2 { margin: 7px 0 0; font: 700 38px/1 Georgia, serif; }
      #curate-feed-comparison header strong { color: #ffd16b; font-size: 30px; }
      #curate-feed-comparison .pair { min-height: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
      #curate-feed-comparison figure { position: relative; min-height: 0; margin: 0; overflow: hidden; border: 1px solid rgba(247,237,218,.35); border-radius: 18px; background: #070b13; }
      #curate-feed-comparison img { width: 100%; height: 100%; object-fit: cover; object-position: top; }
      #curate-feed-comparison figcaption { position: absolute; left: 14px; top: 14px; padding: 8px 12px; border-radius: 999px; background: rgba(7,11,19,.86); border: 1px solid rgba(255,255,255,.55); font: 900 14px/1 ui-monospace, monospace; letter-spacing: .08em; }
      #curate-feed-comparison p { margin: 0; text-align: center; color: #d8cdb8; font-size: 19px; }
    </style>
    <header><div><small></small><h2></h2></div><strong></strong></header>
    <div class="pair"><figure><img><figcaption>Before</figcaption></figure><figure><img><figcaption>After</figcaption></figure></div><p></p>`;
    overlay.querySelector("small").textContent = `${platformName} · same dummy account`;
    overlay.querySelector("h2").textContent = text.title;
    overlay.querySelector("strong").textContent = text.result;
    const imageNodes = overlay.querySelectorAll("img");
    imageNodes[0].src = images.before;
    imageNodes[1].src = images.after;
    overlay.querySelector("p").textContent = text.detail;
    document.body.append(overlay);
    return Promise.all([...imageNodes].map((image) => image.decode()));
  }, { platformName: platform === "youtube" ? "YouTube" : "Bluesky", images: captures, text: copy });
  await pause(1_000);
  await saveKeyframe(platform === "youtube" ? "05-youtube-before-after" : "06-bluesky-before-after");
  await pause(milliseconds - 1_000);
  await page.evaluate(() => document.querySelector("#curate-feed-comparison")?.remove());
  visibleEvidence.platform_comparisons += 1;
}

async function showManagedAwsProof(milliseconds = 4_500) {
  await page.evaluate((proof) => {
    const overlay = document.createElement("section");
    overlay.id = "curate-aws-proof";
    overlay.innerHTML = `<style>
      #curate-aws-proof { position: fixed; inset: 0; z-index: 100100; display: grid; place-items: center; background: #10192a; color: #f7edda; font-family: system-ui, sans-serif; }
      #curate-aws-proof article { width: 760px; padding: 44px; border: 1px solid rgba(247,237,218,.38); border-radius: 24px; background: #17243a; box-shadow: 0 24px 70px rgba(0,0,0,.35); }
      #curate-aws-proof header { display: flex; justify-content: space-between; align-items: center; gap: 24px; margin-bottom: 28px; }
      #curate-aws-proof h2 { margin: 0; font: 700 40px/1 Georgia, serif; }
      #curate-aws-proof small { padding: 8px 11px; border: 2px solid #c43d45; color: #ef9a9f; font: 800 12px/1 ui-monospace, monospace; letter-spacing: .08em; text-transform: uppercase; transform: rotate(-2deg); }
      #curate-aws-proof dl { margin: 0; display: grid; gap: 10px; }
      #curate-aws-proof dl div { display: flex; justify-content: space-between; padding: 15px 18px; border-radius: 12px; background: rgba(7,11,19,.44); }
      #curate-aws-proof dt { color: #d8cdb8; }
      #curate-aws-proof dd { margin: 0; color: #8ee0b8; font-weight: 900; }
    </style><article><header><h2></h2><small></small></header><dl></dl></article>`;
    overlay.querySelector("h2").textContent = proof.title;
    overlay.querySelector("small").textContent = proof.stamp;
    const list = overlay.querySelector("dl");
    for (const [label, value] of proof.rows) {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = label;
      description.textContent = value;
      row.append(term, description);
      list.append(row);
    }
    document.body.append(overlay);
  }, managedAwsProof);
  visibleEvidence.managed_aws_receipt_shown = true;
  await pause(1_000);
  await saveKeyframe("03-aws-managed-proof");
  await pause(milliseconds - 1_000);
  await page.evaluate(() => document.querySelector("#curate-aws-proof")?.remove());
}

async function showAgentWorking() {
  await page.evaluate(() => {
    const overlay = document.createElement("section");
    overlay.id = "curate-agent-working";
    overlay.innerHTML = `<style>
      #curate-agent-working { position: fixed; inset: 0; z-index: 100100; display: grid; place-items: center; background: rgba(16,25,42,.97); color: #f7edda; font-family: system-ui, sans-serif; }
      #curate-agent-working article { width: 720px; }
      #curate-agent-working h2 { margin: 0 0 26px; font: 700 46px/1 Georgia, serif; }
      #curate-agent-working ol { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
      #curate-agent-working li { padding: 16px 18px; border-radius: 13px; background: #17243a; color: #d8cdb8; opacity: .48; animation: curateStep 3.6s infinite; }
      #curate-agent-working li:nth-child(2) { animation-delay: 1.2s; } #curate-agent-working li:nth-child(3) { animation-delay: 2.4s; }
      #curate-agent-working b { color: #f7edda; }
      @keyframes curateStep { 0%, 24% { opacity: .45; transform: translateX(0); } 30%, 62% { opacity: 1; transform: translateX(8px); box-shadow: inset 6px 0 #c43d45; } 70%, 100% { opacity: .45; transform: translateX(0); } }
    </style><article><h2>Curate is working</h2><ol><li><b>Understanding</b> your request</li><li><b>Mapping</b> it to each platform</li><li><b>Checking</b> the expected shift</li></ol></article>`;
    document.body.append(overlay);
  });
  await pause(4_200);
  await saveKeyframe("02-agent-working");
}

async function waitWithoutRecording(label, action, completion) {
  await action();
  await showAgentWorking();
  const startMs = elapsed();
  await completion();
  const endMs = elapsed();
  if (endMs - startMs >= 1_000) condensedWaits.push({ label, start_ms: startMs, end_ms: endMs });
  await page.evaluate(() => document.querySelector("#curate-agent-working")?.remove());
  await pause(700);
}

try {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator(".desk-tabs").waitFor({ state: "visible", timeout: 30_000 });
  await pause(2_000);
  await showChapter("starting-feeds");
  await showCapturedFeeds("before");
  await showChapter("describe");
  await openDesk("evidence");

  const textboxes = page.getByRole("textbox");
  await textboxes.nth(0).fill(vagueGoal);
  await textboxes.nth(1).fill(beforeLinks);
  visibleEvidence.vague_goal_shown = (await textboxes.nth(0).inputValue()) === vagueGoal;
  await showGuide("Write it naturally", "A few links are enough. Descriptions are optional.", 3_200);
  await page.getByTestId("evidence-capture").click();
  await page.getByTestId("feed-before").waitFor({ timeout: 15_000 });
  await center(page.getByTestId("feed-before"), 4_500);
  visibleEvidence.starting_feed_cards = await page.getByTestId("feed-before").locator("article").count();
  visibleEvidence.starting_youtube_cards = await page.getByTestId("feed-before").locator('[data-platform="youtube"]').count();
  visibleEvidence.starting_bluesky_cards = await page.getByTestId("feed-before").locator('[data-platform="bluesky"]').count();
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
  await center(page.locator(".target-stamp-grid"), 6_000);
  await page.getByTestId("evidence-apply").click();
  await page.locator('.curate-result[data-proposal-status="applied_to_passport"]').waitFor({ timeout: 15_000 });
  await pause(2_500);

  await showChapter("agent");
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
  await showManagedAwsProof();
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
  await showChapter("results");
  await openDesk("evidence");

  const updatedTextboxes = page.getByRole("textbox");
  await updatedTextboxes.nth(1).fill(afterLinks);
  await pause(2_500);
  await page.getByTestId("evidence-compare").click();
  await page.getByTestId("feed-after").waitFor({ timeout: 15_000 });
  await center(page.getByTestId("feed-after"), 7_000);
  visibleEvidence.curated_feed_cards = await page.getByTestId("feed-after").locator("article").count();
  visibleEvidence.curated_youtube_cards = await page.getByTestId("feed-after").locator('[data-platform="youtube"]').count();
  visibleEvidence.curated_bluesky_cards = await page.getByTestId("feed-after").locator('[data-platform="bluesky"]').count();
  await showCapturedFeeds("after");
  await showPlatformComparison("youtube");
  await showPlatformComparison("bluesky");

  await showChapter("copy");
  await openDesk("migration");
  await showGuide("Choose the route", "Copy from YouTube to Bluesky, then preview what carries over.", 2_800);
  const migrationSelects = page.locator(".route-ticket select");
  await migrationSelects.nth(0).selectOption("youtube");
  await migrationSelects.nth(1).selectOption("bluesky");
  await page.getByTestId("migration-preview").click();
  await page.locator(".manifest-actions").waitFor({ timeout: 15_000 });
  visibleEvidence.copy_feed_previewed = true;
  await center(page.locator(".translation-loss"), 4_500);
  visibleEvidence.copy_feed_result_shown = true;
  await showGuide("Translation ready", "Curate shows what transfers and what needs a different route before you continue.", 3_500);
  visibleEvidence.tutorial_features_shown.push("tune", "copy");

  await showChapter("incognito");
  await openDesk("temporary");
  await showGuide("Pick a purpose and duration", "This temporary feed can expire on its own.", 2_800);
  await page.getByTestId("temporary-issue").click();
  await page.locator(".temporary-visa.active").waitFor({ timeout: 15_000 });
  visibleEvidence.incognito_issued = true;
  await center(page.locator(".temporary-visa.active"), 6_000);
  await page.getByTestId("temporary-revoke").click();
  await page.locator(".temporary-visa.revoked").waitFor({ timeout: 15_000 });
  visibleEvidence.incognito_revoked = true;
  await showGuide("Closed", "Your usual Passport is unchanged.", 3_000);
  visibleEvidence.tutorial_features_shown.push("incognito");

  await showChapter("blend");
  await openDesk("companion");
  await page.getByText("Shape the shared view").waitFor();
  visibleEvidence.blend_page_shown = true;
  await showGuide("Choose what to share", "Both people keep separate accounts and Passports.", 2_800);
  await page.locator('input[placeholder="HARBOR-1936"]').fill("CURATE-DEMO");
  await page.getByRole("button", { name: "ISSUE COMPANION INVITATION" }).click();
  await page.locator(".second-principal-consent").waitFor({ timeout: 15_000 });
  visibleEvidence.blend_invitation_created = true;
  await showGuide("Second person chooses", "Only the selected tastes enter this shared view.", 2_800);
  await page.getByRole("button", { name: "ACTIVATE SHARED VIEW" }).click();
  await page.locator(".companion-active").waitFor({ timeout: 15_000 });
  visibleEvidence.blend_activated = true;
  await showGuide("Shared view active", "The mix stays temporary and can be stopped at any time.", 3_800);
  await page.getByRole("button", { name: "STOP COMPANION SYNC" }).click();
  await page.locator(".companion-active").waitFor({ state: "detached", timeout: 15_000 });
  visibleEvidence.blend_stopped = true;
  await showGuide("Shared view closed", "Both original Passports remain separate.", 2_800);
  visibleEvidence.tutorial_features_shown.push("blend");
  await page.getByRole("button", { name: "Open Curate passport" }).click();
  await pause(5_000);
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
  schema: "curate/silent-demo-capture/v5",
  generated_at: new Date().toISOString(),
  source,
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
  platform_feed_captures: publicCaptureAttestation(platformCaptureEvidence),
  visible_evidence: visibleEvidence,
  local_passport_revised: true,
  local_mission: missionProof,
  managed_aws_proof: managedProof,
  tutorial: {
    runtime_bounds_ms: DEMO_RUNTIME_BOUNDS_MS,
    chapters: demoChapters.map(({ id }) => id),
    features: tutorialFeatures.map(({ id }) => id),
  },
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
  social_account_accessed_during_recording: false,
  social_action_executed_during_recording: false,
  aws_invoked_during_recording: false,
  external_requests: externalRequests,
  console_errors: consoleErrors,
  page_errors: pageErrors,
  passed: visibleEvidence.vague_goal_shown
    && visibleEvidence.exact_goal_shown
    && visibleEvidence.starting_feed_cards >= 3
    && visibleEvidence.curated_feed_cards >= 3
    && visibleEvidence.starting_youtube_cards >= 3
    && visibleEvidence.starting_bluesky_cards >= 3
    && visibleEvidence.curated_youtube_cards >= 3
    && visibleEvidence.curated_bluesky_cards >= 3
    && visibleEvidence.copy_feed_previewed
    && visibleEvidence.copy_feed_result_shown
    && visibleEvidence.incognito_issued
    && visibleEvidence.incognito_revoked
    && visibleEvidence.blend_page_shown
    && visibleEvidence.blend_invitation_created
    && visibleEvidence.blend_activated
    && visibleEvidence.blend_stopped
    && visibleEvidence.actual_platform_pages_before === 2
    && visibleEvidence.actual_platform_pages_after === 2
    && visibleEvidence.platform_scroll_sequences === 4
    && visibleEvidence.platform_comparisons === 2
    && visibleEvidence.feed_labels_shown >= 8
    && new Set(visibleEvidence.tutorial_chapters_shown).size === demoChapters.length
    && new Set(visibleEvidence.tutorial_features_shown).size === tutorialFeatures.length
    && visibleEvidence.managed_aws_receipt_shown
    && managedProof.available
    && managedProof.account_changes === false
    && missionProof.planned_with_local_model
    && missionProof.executed_on_local_twin
    && missionProof.rollback_state_verified
    && edit.outputDurationMs >= DEMO_RUNTIME_BOUNDS_MS.minimum
    && edit.outputDurationMs <= DEMO_RUNTIME_BOUNDS_MS.maximum
    && edit.outputWidth === 1440
    && edit.outputHeight === 900
    && externalRequests.length === 0
    && consoleErrors.length === 0
    && pageErrors.length === 0,
};
await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
if (!report.passed) throw new Error(`Silent demo capture failed; inspect ${reportPath}`);
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
