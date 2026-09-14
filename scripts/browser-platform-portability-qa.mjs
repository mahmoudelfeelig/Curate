import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";

import { chromium } from "playwright-core";


const projectRoot = path.resolve(import.meta.dirname, "..");
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5178";
const apiUrl = process.env.FEED_PASSPORT_API_URL || "http://127.0.0.1:8010";
const requestedRunName = process.argv.find((value) => value.startsWith("--run="))?.slice(6)
  || `platform-portability-${Date.now()}`;
if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(requestedRunName)) {
  throw new Error("Run name must use 1-64 lowercase letters, digits, underscores, or hyphens.");
}
const runName = requestedRunName;
const outputDirectory = path.join(projectRoot, "artifacts", "browser-qa", runName);
const fixturePath = path.join(
  projectRoot,
  "services",
  "curator",
  "tests",
  "fixtures",
  "instagram",
  "accounts_center_following.json",
);

function assertExactLoopbackOrigin(value, label) {
  const parsed = new URL(value);
  if (
    parsed.protocol !== "http:"
    || parsed.hostname !== "127.0.0.1"
    || !parsed.port
    || parsed.username
    || parsed.password
    || !["", "/"].includes(parsed.pathname)
    || parsed.search
    || parsed.hash
  ) {
    throw new Error(`${label} must be an exact http://127.0.0.1:<port> origin.`);
  }
}

async function assertOwnedDisposableTarget() {
  const markerPath = process.env.FEED_PASSPORT_QA_DISPOSABLE_MARKER_FILE;
  const markerToken = process.env.FEED_PASSPORT_QA_DISPOSABLE_TOKEN;
  if (!markerPath || !markerToken) {
    throw new Error(
      "Refusing to mutate an unverified API target. Run `npm run test:browser:platforms`; "
      + "the owning harness supplies a disposable database and target marker.",
    );
  }
  assertExactLoopbackOrigin(baseUrl, "Browser URL");
  assertExactLoopbackOrigin(apiUrl, "API URL");
  const resolvedMarker = await fs.realpath(markerPath);
  const temporaryRoot = `${path.resolve(os.tmpdir())}${path.sep}`.toLowerCase();
  if (!resolvedMarker.toLowerCase().startsWith(temporaryRoot)) {
    throw new Error("Disposable QA marker must live under the operating-system temporary directory.");
  }
  const marker = JSON.parse(await fs.readFile(resolvedMarker, "utf8"));
  if (
    marker.schema !== "feed-passport/disposable-browser-target/v1"
    || marker.token !== markerToken
    || marker.base_url !== baseUrl
    || marker.api_url !== apiUrl
    || marker.owns_database !== true
    || !/^[a-f0-9]{64}$/.test(marker.api_instance_proof || "")
  ) {
    throw new Error("Disposable QA target marker does not match the requested browser/API origins.");
  }
  const response = await fetch(`${apiUrl}/health`, { signal: AbortSignal.timeout(2_000) });
  if (!response.ok) throw new Error(`Disposable QA API health returned HTTP ${response.status}.`);
  const health = await response.json();
  if (
    health?.qa_disposable_target?.schema !== "feed-passport/disposable-api-target/v1"
    || health.qa_disposable_target.run_token !== markerToken
    || health.qa_disposable_target.database_binding !== marker.api_instance_proof
  ) {
    throw new Error("API did not prove ownership of the disposable QA database.");
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

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
      // Try the next installed local browser.
    }
  }
  throw new Error("No supported local Chromium browser was found.");
}

async function waitIdle(page) {
  await page.waitForFunction(
    () => document.querySelector(".workspace-stage")?.getAttribute("aria-busy") !== "true",
  );
  const alert = page.locator("[role='alert']");
  if (await alert.count()) {
    throw new Error(`Unexpected UI alert: ${await alert.first().innerText()}`);
  }
}

async function openDesk(page, id) {
  const tab = page.locator(`.desk-tabs button[data-section="${id}"]`);
  await tab.click();
  await page.waitForFunction(
    (section) => document.querySelector(`.desk-tabs button[data-section="${section}"]`)
      ?.getAttribute("aria-current") === "page",
    id,
  );
}

async function executeGuidedMigration(page) {
  const responsePromise = page.waitForResponse((response) => {
    const parsed = new URL(response.url());
    return response.request().method() === "POST"
      && /^\/api\/migrations\/[^/]+\/execute$/.test(parsed.pathname);
  });
  await page.getByRole("button", {
    name: /^(?:PREPARE MY STEPS|PREPARE GUIDED HANDOFF|APPLY TRANSLATED PLAN)$/,
  }).click();
  const response = await responsePromise;
  assert(response.ok(), `Migration execution returned HTTP ${response.status()}`);
  return response.json();
}

async function assertExactGuidedHandoff(page, executed, platform) {
  assert(executed?.platform === platform, `Execution returned ${executed?.platform} for ${platform}`);
  const handoff = executed?.guided_handoff;
  assert(handoff?.platform === platform, `Guided handoff is not bound to ${platform}`);
  assert(Array.isArray(handoff.steps) && handoff.steps.length > 0, `${platform} returned no guided steps`);
  const articles = page.locator(".guided-step-list > article");
  assert(await articles.count() === handoff.steps.length, `${platform} rendered a different step count`);
  for (const [index, step] of handoff.steps.entries()) {
    assert(
      typeof step.target === "string" && step.target.trim()
        && typeof step.instruction === "string" && step.instruction.trim(),
      `${platform} returned an empty exact target or instruction`,
    );
    const article = articles.nth(index);
    const heading = (await article.locator(".guided-step-copy > b").innerText()).toLowerCase();
    const instruction = (await article.locator(".guided-step-copy > p").innerText()).trim();
    assert(
      heading.includes(String(step.action_type).replaceAll("_", " ").toLowerCase())
        && heading.includes(step.target.toLowerCase()),
      `${platform} did not render the server-bound action and target at step ${index + 1}`,
    );
    assert(
      instruction === step.instruction.trim(),
      `${platform} did not render the exact server-bound instruction at step ${index + 1}`,
    );
  }
}

async function screenshot(page, name, report) {
  // Full-page screenshots can stitch off-screen fixed elements (including the
  // accessibility skip link) into the middle of the image when a prior locator
  // action scrolled the viewport. Capture from the document origin so the
  // artifact matches the page's normal visual state.
  await page.evaluate(() => {
    window.scrollTo({ top: 0, left: 0, behavior: "instant" });
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  const target = path.join(outputDirectory, `${name}.png`);
  await page.screenshot({ path: target, fullPage: true, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, target).replaceAll("\\", "/"));
}

await assertOwnedDisposableTarget();
await fs.mkdir(path.dirname(outputDirectory), { recursive: true });
await fs.mkdir(outputDirectory, { recursive: false });
const report = {
  schema: "feed-passport/platform-portability-browser-qa/v1",
  generated_at: new Date().toISOString(),
  base_url: baseUrl,
  api_url: apiUrl,
  external_network_allowed: false,
  external_requests: [],
  console_errors: [],
  page_errors: [],
  assertions: [],
  screenshots: [],
  passed: false,
  error: null,
};

const browser = await chromium.launch({
  executablePath: await browserExecutable(),
  headless: true,
  args: ["--disable-background-networking", "--disable-component-update"],
});

try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1024 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  const allowedOrigins = new Set([new URL(baseUrl).origin, new URL(apiUrl).origin]);
  const allowedHosts = new Set([new URL(baseUrl).host, new URL(apiUrl).host]);
  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    const parsed = new URL(requestUrl);
    if (allowedOrigins.has(parsed.origin) || ["blob:", "data:"].includes(parsed.protocol)) {
      await route.continue();
      return;
    }
    report.external_requests.push(requestUrl);
    await route.abort("blockedbyclient");
  });
  await context.routeWebSocket("**/*", async (route) => {
    const websocketUrl = route.url();
    const parsed = new URL(websocketUrl);
    if (["ws:", "wss:"].includes(parsed.protocol) && allowedHosts.has(parsed.host)) {
      route.connectToServer();
      return;
    }
    report.external_requests.push(websocketUrl);
    await route.close({ code: 1008, reason: "External WebSocket blocked by QA" });
  });
  const page = await context.newPage();
  const localImportRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/platform-imports/instagram/preview")) {
      localImportRequests.push(request.url());
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error") report.console_errors.push(message.text());
  });
  page.on("pageerror", (error) => report.page_errors.push(error.message));
  // The development server keeps an HMR channel open, so readiness is defined
  // by the app's own authoritative-hydration marker rather than network idle.
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await waitIdle(page);

  await openDesk(page, "visas");
  const instagramCard = page.locator(".destination-list button, .visa-list button, button")
    .filter({ hasText: /^Instagram/ })
    .first();
  assert(await instagramCard.count() === 1, "Instagram destination control is missing");
  await instagramCard.click();
  await page.locator(".instagram-import-desk").waitFor({ state: "visible" });
  await screenshot(page, "instagram-import-empty-desktop", report);
  report.assertions.push("Instagram local intake is visible only on the Instagram destination.");

  await page.locator(".instagram-import-desk input[type='file']").setInputFiles(fixturePath);
  await waitIdle(page);
  assert(localImportRequests.length === 1, "The browser did not make exactly one local import preview request");
  assert(
    new URL(localImportRequests[0]).searchParams.get("filename") === "following.json",
    "The local import request exposed the fixture's original filename",
  );
  await page.locator(".instagram-import-desk").getByText("READY", { exact: true }).waitFor();
  assert(
    await page.locator(".instagram-handle-manifest label").count() === 2,
    "The recognized export did not render exactly two handles",
  );
  assert(
    (await page.locator(".instagram-import-unobserved").innerText()).includes("recommendation state"),
    "The importer did not disclose its unobserved ranking boundary",
  );
  await screenshot(page, "instagram-import-preview-desktop", report);
  report.assertions.push("The strict export preview rendered two normalized handles and no inferred ranking state.");
  report.assertions.push("The upload transport replaced the private local filename with a generic parser hint.");

  await page.locator(".instagram-handle-manifest label").filter({ hasText: "@paper.lab" }).click();
  assert(
    await page.locator(".instagram-import-consent input").count() === 0,
    "The import flow reintroduced a redundant confirmation checkbox",
  );
  await page.getByRole("button", { name: "ADD SELECTED TO PASSPORT" }).click();
  await waitIdle(page);
  await page.locator(".instagram-import-desk").getByText("CONSUMED", { exact: true }).waitFor();
  assert(
    await page.locator(".instagram-handle-manifest").count() === 0,
    "Consumed import retained the private handle-selection surface",
  );
  report.assertions.push("One selected creator revised the Passport and consumed the private session.");
  report.assertions.push("The selected import applied directly without a redundant confirmation checkbox.");

  await page.setViewportSize({ width: 390, height: 844 });
  const bodyWidth = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  assert(bodyWidth.scroll <= bodyWidth.client + 1, "Consumed Instagram view overflows mobile width");
  await screenshot(page, "instagram-import-consumed-mobile", report);
  report.assertions.push("The consumed state has no horizontal overflow at 390 CSS pixels.");

  await page.setViewportSize({ width: 1440, height: 1024 });
  await openDesk(page, "migration");
  const routeSelects = page.locator(".route-ticket select");
  assert(await routeSelects.count() === 2, "Migration route selectors are missing");
  await routeSelects.nth(0).selectOption("lab");
  await routeSelects.nth(1).selectOption("instagram");
  await page.getByRole("button", { name: "PREVIEW TRANSLATION" }).click();
  await waitIdle(page);
  const instagramExecution = await executeGuidedMigration(page);
  await waitIdle(page);
  await page.locator(".guided-handoff-panel").waitFor({ state: "visible" });
  await assertExactGuidedHandoff(page, instagramExecution, "instagram");
  assert(
    await page.locator(".guided-step-actions").count() > 0,
    "Instagram migration did not stop for an exact native-control handoff",
  );
  await screenshot(page, "instagram-guided-handoff-desktop", report);

  while (await page.getByRole("button", { name: "CONTROL NOT FOUND" }).count()) {
    await page.getByRole("button", { name: "CONTROL NOT FOUND" }).first().click();
    await waitIdle(page);
  }
  await page.getByRole("button", { name: "FINALIZE USER RECORD" }).click();
  await waitIdle(page);
  const summary = await page.locator(".guided-receipt-summary").innerText();
  assert(/API writes\s*0/i.test(summary), "Guided receipt did not disclose zero API writes");
  assert(
    summary.includes("0 recommendation outcomes verified"),
    "Guided receipt overstated recommendation verification",
  );
  await screenshot(page, "instagram-guided-finalized-desktop", report);
  report.assertions.push("The account-free Instagram run recorded only control-not-found attestations with zero API writes.");

  for (const platform of ["youtube", "bluesky"]) {
    const selectors = page.locator(".route-ticket select");
    await selectors.nth(0).selectOption("lab");
    await selectors.nth(1).selectOption(platform);
    await page.getByRole("button", { name: "PREVIEW TRANSLATION" }).click();
    await waitIdle(page);
    const executed = await executeGuidedMigration(page);
    await waitIdle(page);
    await page.locator(".guided-handoff-panel").waitFor({ state: "visible" });
    await assertExactGuidedHandoff(page, executed, platform);
    while (await page.getByRole("button", { name: "CONTROL NOT FOUND" }).count()) {
      await page.getByRole("button", { name: "CONTROL NOT FOUND" }).first().click();
      await waitIdle(page);
    }
    await page.getByRole("button", { name: "FINALIZE USER RECORD" }).click();
    await waitIdle(page);
    const guidedSummary = await page.locator(".guided-receipt-summary").innerText();
    assert(/API writes\s*0/i.test(guidedSummary), `${platform} guided record overstated API writes`);
    assert(
      guidedSummary.includes("0 recommendation outcomes verified"),
      `${platform} guided record overstated recommendation verification`,
    );
    report.assertions.push(`The account-free ${platform} flow exposed exact native controls and finalized only zero-write control-not-found attestations.`);
  }

  await screenshot(page, "youtube-bluesky-guided-boundary-desktop", report);

  assert(report.external_requests.length === 0, "The browser attempted an external request");
  assert(report.console_errors.length === 0, "The browser emitted console errors");
  assert(report.page_errors.length === 0, "The page emitted uncaught errors");
  report.passed = true;
  await context.close();
} catch (error) {
  report.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
} finally {
  await browser.close();
  await fs.writeFile(
    path.join(outputDirectory, "report.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    "utf8",
  );
}

process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
