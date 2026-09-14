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
  autonomousRunStory,
  demoChapters,
  demoPersonas,
  demoVideo,
  exactTopicTarget,
  managedAwsProof,
  platformFeedStory,
  practiceFeedTour,
  tutorialFeatures,
  validateDemoStoryboard,
} from "./demo-storyboard.mjs";

const projectRoot = path.resolve(import.meta.dirname, "..");
const execFileAsync = promisify(execFile);
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5173";
const apiUrl = process.env.FEED_PASSPORT_API_URL || "http://127.0.0.1:8000";
const acknowledgement = process.env.FEED_PASSPORT_DEMO_RECORDING_ACK || "";
const platformCaptureManifestPath = process.env.FEED_PASSPORT_PLATFORM_CAPTURE_MANIFEST || "";
const awsScreenshotPath = process.env.FEED_PASSPORT_AWS_SCREENSHOT || "";
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

const personaById = Object.fromEntries(demoPersonas.map((persona) => [persona.id, persona]));
const vagueGoal = personaById["tune-vague"].prompt;
const exactGoal = personaById["precise-mix"].prompt;
const agentGoal = "I run a small creative studio. Cut the ragebait and give me more research, independent creators, thoughtful design, and local culture.";
const beforeLinks = [
  "https://www.youtube.com/watch?v=b4RageBt001",
  "https://www.youtube.com/watch?v=b4DramaBt02",
  "https://www.youtube.com/watch?v=b4SpaceSc03",
  "https://bsky.app/profile/noise.curate/post/3before01",
  "https://bsky.app/profile/noise.curate/post/3before02",
  "https://bsky.app/profile/noise.curate/post/3before03",
].join("\n");
const afterLinks = [
  "https://www.youtube.com/watch?v=afSpaceSc01",
  "https://www.youtube.com/watch?v=afCodeSci02",
  "https://www.youtube.com/watch?v=afDrawArt03",
  "https://bsky.app/profile/anime.curate/post/3after001",
  "https://bsky.app/profile/manga.curate/post/3after002",
  "https://bsky.app/profile/scent.curate/post/3after003",
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
    <canvas id="canvas" width="${demoVideo.width}" height="${demoVideo.height}"></canvas>
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
  const encoding = editPage.evaluate(async ({ requestedSegments, videoBitrate }) => {
    const video = document.querySelector("#video");
    const canvas = document.querySelector("#canvas");
    const drawing = canvas.getContext("2d", { alpha: false });
    const stream = canvas.captureStream(30);
    const preferredTypes = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
    const mimeType = preferredTypes.find((type) => MediaRecorder.isTypeSupported(type));
    if (!mimeType) throw new Error("This browser cannot encode a WebM canvas recording.");
    const chunks = [];
    const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: videoBitrate });
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
  }, { requestedSegments: segments, videoBitrate: demoVideo.bitrate });
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
const expectedPlatformSequenceCount = platformCaptureEvidence.captures.length;
const expectedPlatformFrames = platformCaptureEvidence.captures.reduce(
  (total, capture) => total + capture.frames.length,
  0,
);
const expectedReviewedUniqueItems = platformCaptureEvidence.captures.reduce(
  (total, capture) => total + (capture.reviewed_unique_feed_items || 0),
  0,
);
async function managedProofAttestation() {
  const [healthReceipt, planReceipt] = await Promise.all([
    fs.readFile(path.join(managedProofDirectory, "managed-health-receipt.json"), "utf8").then(JSON.parse),
    fs.readFile(path.join(managedProofDirectory, "managed-plan-feed-receipt.json"), "utf8").then(JSON.parse),
  ]);
  const tools = Array.isArray(planReceipt.result?.evidence?.tools) ? planReceipt.result.evidence.tools : [];
  const toolNames = tools.map(({ name }) => name);
  const targetWeights = planReceipt.result?.proposal?.target_topic_weights || {};
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
    && planReceipt.result?.evidence?.explicit_percentages_enforced === true
    && planReceipt.result?.evidence?.cycles === 3
    && planReceipt.result?.evidence?.duration_ms === 3359
    && healthReceipt.result?.model?.model_id === "amazon.nova-lite-v1:0"
    && JSON.stringify(toolNames) === JSON.stringify([
      "inspect_selected_passport",
      "inspect_sanitized_evidence",
      "submit_feed_goal_proposal",
    ])
    && exactTopicTarget.every(([topic, percent]) => Math.round(Number(targetWeights[topic]) * 100) === percent);
  if (!valid) throw new Error("The redacted managed AgentCore proof is incomplete or unsafe to present.");
  return {
    available: true,
    health: "healthy",
    plan_returned: true,
    exact_percentages_preserved: true,
    account_changes: false,
    model_id: "amazon.nova-lite-v1:0",
    planning_cycles: 3,
    duration_ms: 3359,
    tools: tools.map(({ name, status }) => ({ name, status })),
    target_topics: exactTopicTarget.map(([topic, percent]) => ({ topic, percent })),
    current_source: healthReceipt.source_commit === source.git_revision
      && planReceipt.source_commit === source.git_revision,
  };
}
const managedProof = await managedProofAttestation();
async function loadAwsScreenshot() {
  if (!awsScreenshotPath) return null;
  const absolutePath = path.resolve(awsScreenshotPath);
  const extension = path.extname(absolutePath).toLowerCase();
  const mimeType = extension === ".png"
    ? "image/png"
    : [".jpg", ".jpeg"].includes(extension)
      ? "image/jpeg"
      : extension === ".webp"
        ? "image/webp"
        : null;
  if (!mimeType) throw new Error("FEED_PASSPORT_AWS_SCREENSHOT must be a PNG, JPEG, or WebP image.");
  const bytes = await fs.readFile(absolutePath);
  if (bytes.length < 10_000) throw new Error("The AWS screenshot is unexpectedly small.");
  return {
    dataUrl: `data:${mimeType};base64,${bytes.toString("base64")}`,
    file: path.basename(absolutePath),
    sha256: createHash("sha256").update(bytes).digest("hex"),
  };
}
const awsScreenshot = await loadAwsScreenshot();
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
  platform_sequences_completed: 0,
  platform_frames_shown: 0,
  reviewed_unique_items_shown: 0,
  platform_comparisons: 0,
  feed_labels_shown: 0,
  minimum_labeled_frame_hold_ms: demoVideo.minimumLabeledFrameHoldMs,
  prompt_personalities_shown: [],
  full_screen_explanation_slides: 0,
  tutorial_chapters_shown: [],
  tutorial_features_shown: [],
  managed_aws_receipt_shown: false,
  managed_aws_trace_events: 0,
  aws_console_capture_shown: false,
  aws_cli_fallback_shown: false,
  autonomous_agent_run_shown: false,
  autonomous_agent_actions_shown: 0,
  autonomous_agent_passes_shown: 0,
  computed_target_topics: [],
  computed_target_topic_count: 0,
  computed_target_total_percent: 0,
  practice_feed_transformation_shown: false,
  practice_feed_cards_toured: 0,
  practice_feed_scroll_pixels: 0,
  practice_feed_ragebait_drop_points: 0,
  practice_feed_topics_shown: [],
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
  viewport: { width: demoVideo.width, height: demoVideo.height },
  recordVideo: { dir: outputDirectory, size: { width: demoVideo.width, height: demoVideo.height } },
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

async function showBeat(id, milliseconds = 1_250) {
  const chapter = demoChapters.find((item) => item.id === id);
  if (!chapter) throw new Error(`Unknown demo chapter ${id}.`);
  await page.evaluate((item) => {
    document.querySelector("#curate-demo-beat")?.remove();
    const overlay = document.createElement("aside");
    overlay.id = "curate-demo-beat";
    overlay.innerHTML = `
      <style>
        #curate-demo-beat { position: fixed; left: 22px; top: 22px; z-index: 100100; max-width: 470px; padding: 11px 15px 12px; border: 1px solid rgba(247,237,218,.48); border-left: 6px solid #c43d45; border-radius: 10px; background: rgba(11,16,32,.92); color: #f7edda; box-shadow: 0 12px 32px rgba(0,0,0,.3); font-family: system-ui, sans-serif; pointer-events: none; }
        #curate-demo-beat small { color: #ff9b8e; font: 800 11px/1 ui-monospace, monospace; letter-spacing: .1em; text-transform: uppercase; }
        #curate-demo-beat b { display: block; margin-top: 5px; font-size: 18px; line-height: 1.15; }
        #curate-demo-beat span { display: block; margin-top: 3px; color: #d8cdb8; font-size: 13px; line-height: 1.3; }
      </style>
      <small></small><b></b><span></span>`;
    overlay.querySelector("small").textContent = item.eyebrow;
    overlay.querySelector("b").textContent = item.title;
    overlay.querySelector("span").textContent = item.detail;
    document.body.append(overlay);
  }, chapter);
  visibleEvidence.tutorial_chapters_shown.push(id);
  await pause(milliseconds);
  await page.evaluate(() => document.querySelector("#curate-demo-beat")?.remove());
}

async function showPersona(id, milliseconds = 2_800) {
  const persona = personaById[id];
  if (!persona) throw new Error(`Unknown demo persona ${id}.`);
  await page.evaluate((item) => {
    document.querySelector("#curate-demo-persona")?.remove();
    const note = document.createElement("aside");
    note.id = "curate-demo-persona";
    note.innerHTML = `<style>
      #curate-demo-persona { position: fixed; right: 22px; bottom: 22px; z-index: 100095; width: min(520px, calc(100vw - 44px)); padding: 13px 16px; border: 1px solid rgba(247,237,218,.5); border-radius: 10px; background: rgba(11,16,32,.94); color: #f7edda; box-shadow: 0 12px 32px rgba(0,0,0,.3); font-family: system-ui, sans-serif; pointer-events: none; }
      #curate-demo-persona b { display: block; color: #ffb1a7; font: 800 11px/1 ui-monospace, monospace; letter-spacing: .08em; text-transform: uppercase; }
      #curate-demo-persona q { display: block; margin-top: 7px; font-size: 15px; line-height: 1.35; }
    </style><b></b><q></q>`;
    note.querySelector("b").textContent = item.label;
    note.querySelector("q").textContent = item.prompt;
    document.body.append(note);
  }, persona);
  visibleEvidence.prompt_personalities_shown.push(id);
  await pause(milliseconds);
  await page.evaluate(() => document.querySelector("#curate-demo-persona")?.remove());
}

function frameDataUrl(frame) {
  return `data:${frame.mime_type};base64,${frame.bytes.toString("base64")}`;
}

function labelsForFrame(story, frameIndex, frameCount) {
  return story.labels.filter((label, labelIndex) => {
    if (Number.isInteger(label.frameIndex)) return label.frameIndex === frameIndex;
    if (story.labels.length === 1) return frameIndex === Math.floor(frameCount / 2);
    return Math.round((labelIndex * (frameCount - 1)) / (story.labels.length - 1)) === frameIndex;
  });
}

async function waitForFrame(story, frameIndex, hasLabels) {
  const configured = Number(story.frameHolds?.[frameIndex]);
  const requested = Number.isFinite(configured) ? configured : demoVideo.defaultFrameHoldMs;
  const actual = hasLabels
    ? Math.max(requested * pace, demoVideo.minimumLabeledFrameHoldMs)
    : requested * pace;
  await new Promise((resolve) => setTimeout(resolve, actual));
}

async function showPlatformFeedCapture(capture) {
  const scene = platformFeedStory[capture.platform]?.[capture.phase];
  if (!scene) throw new Error(`Missing storyboard for ${capture.platform}:${capture.phase}.`);
  if (!Array.isArray(capture.frames) || capture.frames.length === 0) {
    throw new Error(`Missing captured frames for ${capture.platform}:${capture.phase}.`);
  }
  const displayName = capture.platform === "youtube" ? "YouTube" : "Bluesky";
  const firstFrame = capture.frames[0];
  const firstLabels = labelsForFrame(scene, 0, capture.frames.length);
  await page.evaluate(({ captureDataUrl, platform, phase, frameCount, framePosition, frameLabels }) => {
    document.querySelector("#platform-feed-capture")?.remove();
    const overlay = document.createElement("section");
    overlay.id = "platform-feed-capture";
    overlay.setAttribute("role", "region");
    overlay.setAttribute("aria-label", `${platform} dummy-account home feed, ${phase}, recorded scroll sequence`);
    const style = document.createElement("style");
    style.textContent = `
      #platform-feed-capture { position: fixed; inset: 0; z-index: 100000; overflow: hidden; background: #0b1020; color: #fff; font-family: system-ui, sans-serif; }
      #platform-feed-capture .feed-stage { position: absolute; inset: 0; overflow: hidden; background: #080c14; }
      #platform-feed-capture .feed-frame { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; object-position: center; opacity: 0; transform: translateY(10px); transition: opacity 180ms ease, transform 260ms cubic-bezier(.22,.74,.24,1); image-rendering: auto; }
      #platform-feed-capture .feed-frame.is-active { opacity: 1; transform: translateY(0); }
      #platform-feed-capture .platform-pill { position: fixed; z-index: 4; right: 24px; top: 22px; padding: 10px 15px; border: 1px solid rgba(255,255,255,.55); border-radius: 999px; background: rgba(11,16,32,.82); backdrop-filter: blur(12px); font: 800 15px/1 ui-monospace, monospace; letter-spacing: .06em; text-transform: uppercase; }
      #platform-feed-capture .frame-progress { position: fixed; z-index: 4; left: 24px; top: 22px; padding: 10px 13px; border: 1px solid rgba(255,255,255,.4); border-radius: 999px; background: rgba(11,16,32,.82); backdrop-filter: blur(12px); font: 750 13px/1 ui-monospace, monospace; }
      #platform-feed-capture .goal { position: fixed; z-index: 4; left: 22px; bottom: 18px; max-width: min(720px, calc(100vw - 44px)); padding: 9px 13px; border: 1px solid rgba(255,255,255,.42); border-radius: 9px; background: rgba(11,16,32,.9); box-shadow: 0 10px 28px rgba(0,0,0,.35); font-size: 14px; font-weight: 800; }
      #platform-feed-capture .labels { position: absolute; inset: 0; z-index: 3; pointer-events: none; }
      #platform-feed-capture .label { position: absolute; left: var(--x); top: var(--y); transform: translate(-50%, -50%); padding: 7px 10px; border: 2px solid currentColor; border-radius: 7px; background: rgba(5,9,17,.94); box-shadow: 0 7px 20px rgba(0,0,0,.42); font: 850 16px/1 system-ui, sans-serif; white-space: nowrap; }
      #platform-feed-capture .label[data-tone="down"] { color: #ff8a92; }
      #platform-feed-capture .label[data-tone="up"] { color: #8ee0b8; }
      #platform-feed-capture .label[data-tone="change"] { color: #ffd16b; }
      #platform-feed-capture .label[data-tone="neutral"] { color: #8ac7ff; }
    `;
    const stage = document.createElement("div");
    stage.className = "feed-stage";
    const image = document.createElement("img");
    image.className = "feed-frame is-active";
    image.alt = `${platform} home feed ${phase}`;
    image.src = captureDataUrl;
    const spareImage = image.cloneNode();
    spareImage.className = "feed-frame";
    spareImage.removeAttribute("src");
    stage.append(image, spareImage);
    const labels = document.createElement("div");
    labels.className = "labels";
    for (const item of frameLabels) {
      const label = document.createElement("span");
      label.className = "label";
      label.dataset.tone = item.tone;
      label.textContent = item.text;
      label.style.setProperty("--x", `${item.x}%`);
      label.style.setProperty("--y", `${item.y}%`);
      labels.append(label);
    }
    const pill = document.createElement("div");
    pill.className = "platform-pill";
    pill.textContent = `${platform} · ${phase} feed`;
    const progress = document.createElement("div");
    progress.className = "frame-progress";
    progress.textContent = `Recorded scroll · ${framePosition}/${frameCount}`;
    const goal = document.createElement("div");
    goal.className = "goal";
    goal.textContent = `Goal: less ragebait · more trustworthy, useful content`;
    overlay.append(style, stage, labels, pill, progress, goal);
    document.body.append(overlay);
    return image.decode();
  }, {
    captureDataUrl: frameDataUrl(firstFrame),
    platform: displayName,
    phase: capture.phase.toUpperCase(),
    frameCount: capture.frames.length,
    framePosition: 1,
    frameLabels: firstLabels,
  });
  visibleEvidence.platform_frames_shown += 1;
  visibleEvidence.feed_labels_shown += firstLabels.length;
  await waitForFrame(scene, 0, firstLabels.length > 0);
  if (capture.platform === "youtube" && capture.phase === "before") await saveKeyframe("01-youtube-before-scroll");

  for (const [frameIndex, frame] of capture.frames.entries()) {
    if (frameIndex === 0) continue;
    const frameLabels = labelsForFrame(scene, frameIndex, capture.frames.length);
    await page.evaluate(async ({ captureDataUrl, framePosition, frameCount, labelsToShow }) => {
      const overlay = document.querySelector("#platform-feed-capture");
      const frames = [...overlay.querySelectorAll(".feed-frame")];
      const current = frames.find((node) => node.classList.contains("is-active"));
      const incoming = frames.find((node) => node !== current);
      incoming.src = captureDataUrl;
      await incoming.decode();
      const labelLayer = overlay.querySelector(".labels");
      labelLayer.replaceChildren();
      for (const item of labelsToShow) {
        const label = document.createElement("span");
        label.className = "label";
        label.dataset.tone = item.tone;
        label.textContent = item.text;
        label.style.setProperty("--x", `${item.x}%`);
        label.style.setProperty("--y", `${item.y}%`);
        labelLayer.append(label);
      }
      incoming.classList.add("is-active");
      current.classList.remove("is-active");
      overlay.querySelector(".frame-progress").textContent = `Recorded scroll · ${framePosition}/${frameCount}`;
    }, {
      captureDataUrl: frameDataUrl(frame),
      framePosition: frameIndex + 1,
      frameCount: capture.frames.length,
      labelsToShow: frameLabels,
    });
    visibleEvidence.platform_frames_shown += 1;
    visibleEvidence.feed_labels_shown += frameLabels.length;
    await waitForFrame(scene, frameIndex, frameLabels.length > 0);
  }
  if (capture.platform === "youtube" && capture.phase === "after") await saveKeyframe("04-youtube-after-scroll");
  await page.evaluate(() => document.querySelector("#platform-feed-capture")?.remove());
  visibleEvidence.platform_sequences_completed += 1;
  visibleEvidence.reviewed_unique_items_shown += capture.reviewed_unique_feed_items || 0;
}

async function showCapturedFeeds(phase) {
  const captures = platformCaptureEvidence.captures.filter((capture) => capture.phase === phase);
  for (const capture of captures) await showPlatformFeedCapture(capture);
  visibleEvidence[`actual_platform_pages_${phase}`] = captures.length;
}

async function showPlatformWipe(platform, milliseconds = 4_200) {
  const selectedCaptures = Object.fromEntries(platformCaptureEvidence.captures
    .filter((capture) => capture.platform === platform)
    .map((capture) => [capture.phase, capture]));
  const comparison = platformFeedStory[platform].comparison;
  const comparisonImages = Object.fromEntries(["before", "after"].map((phase) => {
    const capture = selectedCaptures[phase];
    const configuredIndex = comparison?.[phase]?.frameIndex;
    const frameIndex = Number.isInteger(configuredIndex)
      ? Math.min(capture.frames.length - 1, Math.max(0, configuredIndex))
      : 0;
    return [phase, frameDataUrl(capture.frames[frameIndex])];
  }));
  await page.evaluate(({ platformName, images }) => {
    const overlay = document.createElement("section");
    overlay.id = "curate-feed-wipe";
    overlay.innerHTML = `<style>
      #curate-feed-wipe { position: fixed; inset: 0; z-index: 100050; overflow: hidden; background: #070b13; color: #f7edda; font-family: system-ui, sans-serif; }
      #curate-feed-wipe img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; object-position: center; }
      #curate-feed-wipe .after { clip-path: inset(0 100% 0 0); transition: clip-path 2.7s cubic-bezier(.2,.7,.2,1); }
      #curate-feed-wipe.reveal .after { clip-path: inset(0 0 0 0); }
      #curate-feed-wipe .wipe-line { position: absolute; inset: 0 auto 0 0; width: 3px; background: #ffd16b; box-shadow: 0 0 20px rgba(255,209,107,.7); transition: left 2.7s cubic-bezier(.2,.7,.2,1); }
      #curate-feed-wipe.reveal .wipe-line { left: calc(100% - 3px); }
      #curate-feed-wipe .wipe-label { position: absolute; left: 20px; top: 18px; padding: 9px 12px; border: 1px solid rgba(255,255,255,.5); border-radius: 8px; background: rgba(11,16,32,.9); font: 850 14px/1 ui-monospace, monospace; letter-spacing: .05em; text-transform: uppercase; }
      #curate-feed-wipe .after-label { left: auto; right: 20px; color: #8ee0b8; }
    </style>
    <img class="before" alt=""><img class="after" alt=""><div class="wipe-line"></div><span class="wipe-label before-label"></span><span class="wipe-label after-label"></span>`;
    const imageNodes = overlay.querySelectorAll("img");
    imageNodes[0].src = images.before;
    imageNodes[1].src = images.after;
    overlay.querySelector(".before-label").textContent = `${platformName} · before`;
    overlay.querySelector(".after-label").textContent = `${platformName} · after`;
    document.body.append(overlay);
    return Promise.all([...imageNodes].map((image) => image.decode())).then(() => requestAnimationFrame(() => overlay.classList.add("reveal")));
  }, { platformName: platform === "youtube" ? "YouTube" : "Bluesky", images: comparisonImages });
  await pause(2_900);
  await saveKeyframe(platform === "youtube" ? "05-youtube-before-after" : "06-bluesky-before-after");
  await pause(Math.max(500, milliseconds - 2_900));
  await page.evaluate(() => document.querySelector("#curate-feed-wipe")?.remove());
  visibleEvidence.platform_comparisons += 1;
}

async function collectPracticeFeedTransformation() {
  return page.evaluate(() => {
    const readColumn = (selector) => {
      const root = document.querySelector(selector);
      if (!root) throw new Error(`Missing feed column ${selector}`);
      return {
        metrics: [...root.querySelectorAll(".feed-score-row span")].map((node) => node.textContent.trim()),
        cards: [...root.querySelectorAll(".curate-feed-card")].map((card) => ({
          platform: card.dataset.platform || "feed",
          unwanted: card.classList.contains("is-unwanted"),
          copy: card.querySelector("p")?.textContent.trim() || "Selected post",
          topics: card.querySelector("small")?.textContent.trim() || "Something else",
        })),
      };
    };
    return {
      before: readColumn('[data-testid="feed-before"]'),
      after: readColumn('[data-testid="feed-after"]'),
      changes: [...document.querySelectorAll(".difference-ticket > div > p")].map((node) => ({
        value: node.querySelector("b")?.textContent.trim() || "",
        label: node.querySelector("small")?.textContent.trim() || "",
      })),
    };
  });
}

async function showPracticeFeedTransformation(data) {
  await center(page.getByTestId("feed-before"), 3_600);
  await page.evaluate(() => {
    const firstCard = document.querySelector('[data-testid="feed-before"] .curate-feed-card');
    firstCard?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  await pause(2_200);
  await showBeat("agent", 1_000);
  await center(page.getByTestId("feed-after"), 4_500);
  await page.evaluate(() => {
    const lastCard = document.querySelector('[data-testid="feed-after"] .curate-feed-card:last-of-type');
    lastCard?.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  await pause(2_500);
  await saveKeyframe("04-practice-feed-after-scroll");
  const difference = page.locator(".difference-ticket");
  if (await difference.count()) await center(difference, 4_000);
  visibleEvidence.practice_feed_transformation_shown = true;
  visibleEvidence.practice_feed_cards_toured = data.before.cards.length + data.after.cards.length;
  visibleEvidence.practice_feed_scroll_pixels = practiceFeedTour.minimumScrollPixels;
  visibleEvidence.practice_feed_ragebait_drop_points = Number(data.changes.find(({ label }) => /ragebait/i.test(label))?.value || 0);
  visibleEvidence.practice_feed_topics_shown = [...new Set(data.after.cards.flatMap(({ topics }) => topics
    .split("·")
    .map((topic) => topic.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""))
    .filter(Boolean)))];
}

async function showManagedAwsProof(milliseconds = 10_500) {
  if (awsScreenshot) {
    await page.evaluate(async ({ captureDataUrl }) => {
      const overlay = document.createElement("section");
      overlay.id = "curate-aws-capture";
      overlay.innerHTML = `<style>
        #curate-aws-capture { position: fixed; inset: 0; z-index: 100100; overflow: hidden; background: #fff; }
        #curate-aws-capture img { width: 100%; height: 100%; object-fit: contain; object-position: center; }
        #curate-aws-capture aside { position: absolute; left: 20px; bottom: 18px; padding: 9px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,.52); background: rgba(11,16,32,.92); color: #f7edda; font: 800 13px/1.2 ui-monospace, monospace; }
      </style><img alt="Reviewed AWS CloudWatch console capture"><aside>AWS CLOUDWATCH · RETAINED AGENTCORE RUN</aside>`;
      const image = overlay.querySelector("img");
      image.src = captureDataUrl;
      document.body.append(overlay);
      await image.decode();
    }, { captureDataUrl: awsScreenshot.dataUrl });
    visibleEvidence.aws_console_capture_shown = true;
  } else {
    await page.evaluate(({ presentation }) => {
      const note = document.createElement("aside");
      note.id = "curate-aws-cli-proof";
      note.innerHTML = `<style>
        #curate-aws-cli-proof { position: fixed; right: 20px; bottom: 18px; z-index: 100100; width: min(610px, calc(100vw - 40px)); padding: 14px 16px; border: 1px solid rgba(138,199,255,.5); border-radius: 10px; background: rgba(7,11,19,.96); color: #f7edda; box-shadow: 0 14px 38px rgba(0,0,0,.38); font: 700 12px/1.35 ui-monospace, monospace; }
        #curate-aws-cli-proof header { display: flex; justify-content: space-between; color: #8ac7ff; margin-bottom: 8px; }
        #curate-aws-cli-proof p { display: grid; grid-template-columns: 58px 1fr auto; gap: 10px; margin: 5px 0; }
        #curate-aws-cli-proof b { color: #8ee0b8; }
        #curate-aws-cli-proof small { display: block; margin-top: 8px; color: #9badc8; }
      </style><header><span>AWS CLI LOG EVIDENCE</span><span>RETAINED RUN</span></header><div></div><small>CloudWatch screenshot not supplied · showing the retained redacted CLI receipt</small>`;
      const list = note.querySelector("div");
      for (const event of presentation.cloudWatchEvents) {
        const row = document.createElement("p");
        row.innerHTML = `<b></b><span></span><time></time>`;
        row.querySelector("b").textContent = event.operation;
        row.querySelector("span").textContent = event.message;
        row.querySelector("time").textContent = event.duration;
        list.append(row);
      }
      document.body.append(note);
    }, { presentation: managedAwsProof });
    visibleEvidence.aws_cli_fallback_shown = true;
  }
  visibleEvidence.managed_aws_receipt_shown = true;
  visibleEvidence.managed_aws_trace_events = managedAwsProof.events.length;
  await pause(5_200);
  await saveKeyframe("03-aws-managed-proof");
  await pause(Math.max(500, milliseconds - 5_200));
  await page.evaluate(() => {
    document.querySelector("#curate-aws-capture")?.remove();
    document.querySelector("#curate-aws-cli-proof")?.remove();
  });
}

async function showAgentWorking() {
  await page.evaluate(() => {
    const overlay = document.createElement("aside");
    overlay.id = "curate-agent-working";
    overlay.innerHTML = `<style>
      #curate-agent-working { position: fixed; right: 20px; bottom: 18px; z-index: 100100; width: 430px; padding: 14px 16px; border: 1px solid rgba(247,237,218,.45); border-left: 6px solid #c43d45; border-radius: 10px; background: rgba(11,16,32,.95); color: #f7edda; box-shadow: 0 14px 38px rgba(0,0,0,.35); font-family: system-ui, sans-serif; }
      #curate-agent-working h2 { margin: 0 0 10px; font: 750 19px/1.1 Georgia, serif; }
      #curate-agent-working ol { list-style: none; padding: 0; margin: 0; display: flex; gap: 6px; }
      #curate-agent-working li { padding: 7px 8px; border-radius: 6px; background: #17243a; color: #d8cdb8; font-size: 11px; opacity: .48; animation: curateStep 3.6s infinite; }
      #curate-agent-working li:nth-child(2) { animation-delay: 1.2s; } #curate-agent-working li:nth-child(3) { animation-delay: 2.4s; }
      #curate-agent-working b { color: #f7edda; }
      @keyframes curateStep { 0%, 24% { opacity: .45; transform: translateX(0); } 30%, 62% { opacity: 1; transform: translateX(8px); box-shadow: inset 6px 0 #c43d45; } 70%, 100% { opacity: .45; transform: translateX(0); } }
    </style><article><h2>Curate is working</h2><ol><li><b>Understanding</b> your request</li><li><b>Mapping</b> it to each platform</li><li><b>Checking</b> the expected shift</li></ol></article>`;
    document.body.append(overlay);
  });
  await pause(3_200);
  await saveKeyframe("02-agent-working");
}

async function showRunStatus(run, milliseconds = 5_400) {
  await page.evaluate(({ story, runEvidence }) => {
    const overlay = document.createElement("aside");
    overlay.id = "curate-run-status";
    overlay.innerHTML = `<style>
      #curate-run-status { position: fixed; left: 20px; right: 20px; bottom: 18px; z-index: 100110; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 15px; border: 1px solid rgba(142,224,184,.52); border-radius: 10px; background: rgba(7,11,19,.95); color: #f7edda; box-shadow: 0 14px 38px rgba(0,0,0,.35); font: 750 12px/1.2 ui-monospace, monospace; }
      #curate-run-status .route { display: flex; gap: 7px; }
      #curate-run-status .route b { padding: 6px 8px; border-radius: 6px; background: #17243a; color: #ffd16b; }
      #curate-run-status .result { display: flex; gap: 12px; color: #8ee0b8; }
    </style><div class="route"></div><div class="result"><span class="actions"></span><span class="passes"></span><span class="stop"></span></div>`;
    const route = overlay.querySelector(".route");
    story.steps.forEach((step) => {
      const label = document.createElement("b");
      label.textContent = step.label;
      route.append(label);
    });
    overlay.querySelector(".actions").textContent = `${runEvidence.actions} controls`;
    overlay.querySelector(".passes").textContent = `${runEvidence.passes} measured pass${runEvidence.passes === 1 ? "" : "es"}`;
    overlay.querySelector(".stop").textContent = runEvidence.stop;
    document.body.append(overlay);
  }, { story: autonomousRunStory, runEvidence: run });
  visibleEvidence.autonomous_agent_run_shown = true;
  visibleEvidence.autonomous_agent_actions_shown = run.actions;
  visibleEvidence.autonomous_agent_passes_shown = run.passes;
  await pause(milliseconds);
  await page.evaluate(() => document.querySelector("#curate-run-status")?.remove());
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
  await showBeat("starting-feeds");
  await showCapturedFeeds("before");
  await showBeat("describe");
  await openDesk("evidence");

  const textboxes = page.getByRole("textbox");
  await textboxes.nth(0).fill(vagueGoal);
  await textboxes.nth(1).fill(beforeLinks);
  visibleEvidence.vague_goal_shown = (await textboxes.nth(0).inputValue()) === vagueGoal;
  await showPersona("tune-vague", 3_200);
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
  await showPersona("precise-mix", 3_800);
  await page.getByTestId("evidence-capture").click();
  await page.waitForFunction(
    (priorId) => document.querySelector(".curate-result")?.dataset.proposalId !== priorId,
    previousProposalId,
    { timeout: 15_000 },
  );
  await page.locator(".target-stamp-grid").waitFor({ timeout: 15_000 });
  visibleEvidence.computed_target_topics = await page.locator(".target-stamp-grid article").evaluateAll((cards) => cards.map((card) => ({
    percent: Number(card.querySelector("b")?.textContent.replace("%", "").trim()),
    topic: String(card.querySelector("small")?.textContent || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""),
  })));
  visibleEvidence.computed_target_topic_count = visibleEvidence.computed_target_topics.length;
  visibleEvidence.computed_target_total_percent = visibleEvidence.computed_target_topics.reduce((sum, { percent }) => sum + percent, 0);
  await center(page.locator(".target-stamp-grid"), 6_000);
  await page.getByTestId("evidence-apply").click();
  await page.locator('.curate-result[data-proposal-status="applied_to_passport"]').waitFor({ timeout: 15_000 });
  await pause(2_500);

  await showBeat("agent");
  await openDesk("agent");
  const missionGoal = page.getByRole("textbox", { name: /outcome for the agent|what should curate change/i });
  await missionGoal.fill(agentGoal);
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
  const autonomousRun = await page.evaluate(() => {
    const ledger = document.querySelector(".mission-ledger");
    const terminal = ledger?.querySelector(".mission-terminal span")?.textContent?.trim() || "run complete";
    return {
      actions: ledger?.querySelectorAll(".mission-actions article").length || 0,
      passes: ledger?.querySelectorAll(".iteration-ledger article").length || 0,
      stop: terminal.replaceAll("_", " "),
    };
  });
  if (autonomousRun.actions < 1 || autonomousRun.passes < 1) throw new Error("The autonomous run produced no visible actions or measured passes.");
  await center(page.locator(".mission-comparison"), 5_500);
  await showRunStatus(autonomousRun);
  await page.getByTestId("mission-rollback").click();
  await page.locator('.mission-ledger[data-mission-status="rolled_back"]').waitFor({
    timeout: 30_000,
  });
  await page.getByText(/STATE VERIFIED|STARTING STATE RESTORED|START RESTORED/i).waitFor();
  missionProof.rollback_state_verified = true;
  await pause(3_500);
  await showBeat("results");
  await openDesk("evidence");

  const updatedTextboxes = page.getByRole("textbox");
  await updatedTextboxes.nth(1).fill(afterLinks);
  await pause(2_500);
  await page.getByTestId("evidence-compare").click();
  await page.getByTestId("feed-after").waitFor({ timeout: 15_000 });
  await center(page.getByTestId("feed-after"), 2_000);
  visibleEvidence.curated_feed_cards = await page.getByTestId("feed-after").locator("article").count();
  visibleEvidence.curated_youtube_cards = await page.getByTestId("feed-after").locator('[data-platform="youtube"]').count();
  visibleEvidence.curated_bluesky_cards = await page.getByTestId("feed-after").locator('[data-platform="bluesky"]').count();
  const practiceTransformation = await collectPracticeFeedTransformation();
  await showPracticeFeedTransformation(practiceTransformation);
  await showCapturedFeeds("after");
  await showPlatformWipe("youtube");
  await showPlatformWipe("bluesky");

  await showBeat("copy");
  await openDesk("migration");
  await showPersona("copy", 3_200);
  const migrationSelects = page.locator(".route-ticket select");
  await migrationSelects.nth(0).selectOption("youtube");
  await migrationSelects.nth(1).selectOption("bluesky");
  await page.getByTestId("migration-preview").click();
  await page.locator(".manifest-actions").waitFor({ timeout: 15_000 });
  visibleEvidence.copy_feed_previewed = true;
  await center(page.locator(".translation-loss"), 4_500);
  visibleEvidence.copy_feed_result_shown = true;
  await pause(2_800);
  visibleEvidence.tutorial_features_shown.push("tune", "copy");

  await showBeat("incognito");
  await openDesk("temporary");
  await page.getByLabel("Visa name").fill("Architecture field day");
  await page.getByLabel("Purpose").fill(personaById.incognito.prompt);
  await showPersona("incognito", 3_200);
  await page.getByTestId("temporary-issue").click();
  await page.locator(".temporary-visa.active").waitFor({ timeout: 15_000 });
  visibleEvidence.incognito_issued = true;
  await center(page.locator(".temporary-visa.active"), 6_000);
  await page.getByTestId("temporary-revoke").click();
  await page.locator(".temporary-visa.revoked").waitFor({ timeout: 15_000 });
  visibleEvidence.incognito_revoked = true;
  await pause(2_500);
  visibleEvidence.tutorial_features_shown.push("incognito");

  await showBeat("blend");
  await openDesk("companion");
  await page.getByText("Shape the shared view").waitFor();
  visibleEvidence.blend_page_shown = true;
  await showPersona("blend", 3_200);
  const blendWeight = page.locator('input[type="range"]').last();
  if (await blendWeight.count()) await blendWeight.fill("40");
  await page.locator('input[placeholder="HARBOR-1936"]').fill("CURATE-DEMO");
  await page.getByRole("button", { name: "ISSUE COMPANION INVITATION" }).click();
  await page.locator(".second-principal-consent").waitFor({ timeout: 15_000 });
  visibleEvidence.blend_invitation_created = true;
  await pause(2_500);
  await page.getByRole("button", { name: "ACTIVATE SHARED VIEW" }).click();
  await page.locator(".companion-active").waitFor({ timeout: 15_000 });
  visibleEvidence.blend_activated = true;
  await pause(3_200);
  await page.getByRole("button", { name: "STOP COMPANION SYNC" }).click();
  await page.locator(".companion-active").waitFor({ state: "detached", timeout: 15_000 });
  visibleEvidence.blend_stopped = true;
  await pause(2_500);
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
  schema: "curate/silent-demo-capture/v7",
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
    { kind: "agent_action_goal", text: agentGoal },
  ],
  prompt_personalities: demoPersonas.map(({ id, feature, label, prompt }) => ({
    id,
    feature,
    label,
    prompt,
  })),
  aws_visual_evidence: awsScreenshot
    ? {
        kind: "reviewed_console_capture",
        file: awsScreenshot.file,
        sha256: awsScreenshot.sha256,
      }
    : {
        kind: "retained_redacted_cli_receipt",
        screenshot_supplied: false,
      },
  evidence_classes: {
    recorded_platform_change: {
      input: vagueGoal,
      claim: "Owner-recorded live feed sequences show a changed visible sample after the intervention.",
    },
    computed_passport_target: {
      input: exactGoal,
      topics: visibleEvidence.computed_target_topics,
      claim: "Curate preserved all seven requested percentages and measured them in its practice feed.",
      observed_on_platform: false,
    },
  },
  platform_feed_captures: publicCaptureAttestation(platformCaptureEvidence),
  platform_sequence_summary: {
    sequence_depth_sufficient: platformCaptureEvidence.sequence_depth_sufficient,
    expected_sequences: expectedPlatformSequenceCount,
    expected_frames: expectedPlatformFrames,
    reviewed_unique_feed_items: expectedReviewedUniqueItems,
  },
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
    && platformCaptureEvidence.sequence_depth_sufficient === true
    && visibleEvidence.platform_sequences_completed === expectedPlatformSequenceCount
    && visibleEvidence.platform_frames_shown === expectedPlatformFrames
    && visibleEvidence.reviewed_unique_items_shown === expectedReviewedUniqueItems
    && visibleEvidence.platform_comparisons === 2
    && visibleEvidence.feed_labels_shown >= 24
    && visibleEvidence.minimum_labeled_frame_hold_ms >= 2_500
    && new Set(visibleEvidence.prompt_personalities_shown).size === demoPersonas.length
    && visibleEvidence.full_screen_explanation_slides === 0
    && visibleEvidence.computed_target_topic_count === exactTopicTarget.length
    && visibleEvidence.computed_target_total_percent === 100
    && exactTopicTarget.every(([topic, percent]) => visibleEvidence.computed_target_topics.some((row) => row.topic === topic && row.percent === percent))
    && visibleEvidence.practice_feed_transformation_shown
    && visibleEvidence.practice_feed_cards_toured >= practiceFeedTour.minimumCardsPerPhase * 2
    && visibleEvidence.practice_feed_scroll_pixels >= practiceFeedTour.minimumScrollPixels
    && visibleEvidence.practice_feed_ragebait_drop_points >= practiceFeedTour.expectedRagebaitDropPoints
    && exactTopicTarget.every(([topic]) => visibleEvidence.practice_feed_topics_shown.includes(topic))
    && new Set(visibleEvidence.tutorial_chapters_shown).size === demoChapters.length
    && new Set(visibleEvidence.tutorial_features_shown).size === tutorialFeatures.length
    && visibleEvidence.managed_aws_receipt_shown
    && visibleEvidence.managed_aws_trace_events === managedAwsProof.events.length
    && visibleEvidence.aws_console_capture_shown !== visibleEvidence.aws_cli_fallback_shown
    && visibleEvidence.autonomous_agent_run_shown
    && visibleEvidence.autonomous_agent_actions_shown >= 1
    && visibleEvidence.autonomous_agent_passes_shown >= 1
    && managedProof.available
    && managedProof.account_changes === false
    && missionProof.planned_with_local_model
    && missionProof.executed_on_local_twin
    && missionProof.rollback_state_verified
    && edit.outputDurationMs >= DEMO_RUNTIME_BOUNDS_MS.minimum
    && edit.outputDurationMs <= DEMO_RUNTIME_BOUNDS_MS.maximum
    && edit.outputWidth === demoVideo.width
    && edit.outputHeight === demoVideo.height
    && externalRequests.length === 0
    && consoleErrors.length === 0
    && pageErrors.length === 0,
};
await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
if (!report.passed) throw new Error(`Silent demo capture failed; inspect ${reportPath}`);
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
