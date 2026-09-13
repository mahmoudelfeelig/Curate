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
const runName = `silent-demo-${new Date().toISOString().replaceAll(/[:.]/g, "-")}`;
const outputRoot = process.env.FEED_PASSPORT_DEMO_OUTPUT_DIR || path.join(
  os.tmpdir(),
  "feed-passport-demo-capture",
);
const outputDirectory = path.join(path.resolve(outputRoot), runName);
const videoPath = path.join(outputDirectory, "feed-passport-silent-demo.webm");
const reportPath = path.join(outputDirectory, "report.json");

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
let missionProof = {
  planned_with_local_model: false,
  executed_on_local_twin: false,
  rollback_state_verified: false,
};
const browser = await chromium.launch({
  executablePath: await browserExecutable(),
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

async function openDesk(section) {
  await page.locator(`.desk-tabs button[data-section="${section}"]`).click();
  await page.locator(".workspace-stage").waitFor({ state: "visible" });
  await pause(2_500);
}

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await pause(5_500);
  await openDesk("constitution");
  await pause(4_000);
  await openDesk("evidence");

  const textboxes = page.getByRole("textbox");
  await textboxes.nth(0).fill(
    "Reduce ragebait. Make my feed 60% pet science and 20% cute drawing; "
      + "keep the remainder exploratory.",
  );
  await textboxes.nth(1).fill(
    "https://www.instagram.com/reel/demo-before-pets/ | "
      + "A calm veterinary explanation with a cute drawing of a cat.\n"
      + "https://www.instagram.com/p/demo-before-rage/ | "
      + "Shocking outrage ragebait with no useful context.\n"
      + "https://www.instagram.com/p/demo-before-sketch/ | "
      + "A gentle watercolor sketchbook lesson.",
  );
  await pause(6_000);
  await page.getByRole("button", { name: "CAPTURE BEFORE" }).click();
  await page.getByText("PROPOSED PASSPORT MIX").waitFor({ timeout: 10_000 });
  await pause(9_000);

  await page.getByRole("button", { name: "ASK LOCAL STRANDS AGENT" }).click();
  await page.getByText("STRANDS AGENT PROPOSAL").waitFor({ timeout: localModelWaitMs });
  await pause(10_000);
  await page.getByRole("checkbox", { name: /I reviewed this versioned Passport proposal/ }).check();
  await pause(3_000);
  await page.getByRole("button", { name: "APPLY TO PASSPORT" }).click();
  await page.getByText(/PASSPORT VERSION \d+ SEALED/).waitFor({ timeout: 10_000 });
  await pause(7_000);

  await openDesk("agent");
  const missionGoal = page.getByRole("textbox", { name: "Outcome for the agent" });
  await missionGoal.fill(
    "Apply this Passport to the fresh local YouTube twin, reduce ragebait, "
      + "and stop once the sampled feed is measurably closer.",
  );
  await page.getByRole("button", { name: "ASK LOCAL MODEL TO PLAN" }).click();
  await page.locator('.mission-ledger[data-mission-status="awaiting_approval"]').waitFor({
    timeout: localModelWaitMs,
  });
  await page.getByRole("region", { name: "Local model planner evidence" }).waitFor();
  missionProof.planned_with_local_model = true;
  await pause(8_000);
  await page.getByRole("checkbox", { name: /I approve this bounded local mission policy/ }).check();
  await pause(2_500);
  await page.getByRole("button", { name: "RUN LOCALLY" }).click();
  await page.locator(
    '.mission-ledger[data-mission-status="completed"], '
      + '.mission-ledger[data-mission-status="needs_human"]',
  ).waitFor({ timeout: 30_000 });
  missionProof.executed_on_local_twin = true;
  await pause(8_000);
  await page.getByRole("button", { name: "ROLL BACK RUN" }).click();
  await page.locator('.mission-ledger[data-mission-status="rolled_back"]').waitFor({
    timeout: 30_000,
  });
  await page.getByText("STATE VERIFIED").waitFor();
  missionProof.rollback_state_verified = true;
  await pause(8_000);
  await openDesk("connected-agent");
  await pause(7_000);
  await openDesk("temporary");
  await pause(5_000);
  await openDesk("companion");
  await pause(5_000);
  await openDesk("evidence");

  const updatedTextboxes = page.getByRole("textbox");
  await updatedTextboxes.nth(1).fill(
    "https://www.instagram.com/reel/demo-after-science/ | "
      + "A veterinary study explained with a cute hand-drawn diagram.\n"
      + "https://www.instagram.com/p/demo-after-cats/ | "
      + "Calm animal behavior research about cats.\n"
      + "https://www.instagram.com/p/demo-after-art/ | "
      + "A quiet character art and watercolor lesson.",
  );
  await pause(5_000);
  await page.getByRole("button", { name: "COMPARE AFTER" }).click();
  await page.locator(".evidence-applied b").waitFor({ timeout: 10_000 });
  await pause(10_000);
  await page.getByRole("button", { name: "Open passport overview" }).click();
  await pause(8_000);
} finally {
  await page.close();
  await video.saveAs(videoPath);
  await video.delete();
  await context.close();
  await browser.close();
}

const videoBytes = await fs.readFile(videoPath);
const report = {
  schema: "feed-passport/silent-demo-capture/v1",
  generated_at: new Date().toISOString(),
  video: videoPath,
  sha256: createHash("sha256").update(videoBytes).digest("hex"),
  bytes: videoBytes.length,
  elapsed_ms: Date.now() - recordingStartedAt,
  browser_origin: baseUrl,
  api_origin: apiUrl,
  model: {
    provider: model.provider,
    model_id: model.model_id,
    endpoint_scope: model.endpoint_scope,
    external_model_calls: model.external_model_calls,
    paid_model_calls: model.paid_model_calls,
  },
  local_passport_revised: true,
  local_mission: missionProof,
  social_account_accessed: false,
  social_action_executed: false,
  external_requests: externalRequests,
  console_errors: consoleErrors,
  page_errors: pageErrors,
  passed: missionProof.planned_with_local_model
    && missionProof.executed_on_local_twin
    && missionProof.rollback_state_verified
    && externalRequests.length === 0
    && consoleErrors.length === 0
    && pageErrors.length === 0,
};
await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
if (!report.passed) throw new Error(`Silent demo capture failed; inspect ${reportPath}`);
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
