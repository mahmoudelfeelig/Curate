import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const projectRoot = path.resolve(import.meta.dirname, "..");
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5173";
const runName = process.argv.find((argument) => argument.startsWith("--run="))?.split("=")[1] || "flow-fixture";
const outputDir = path.join(projectRoot, "artifacts", "browser-qa", runName);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    try {
      return await import("playwright-core");
    } catch {
      // Fall through to the Codex-bundled runtime when working inside Codex.
    }
    const runtimeModule = process.env.FEED_PASSPORT_PLAYWRIGHT_MODULE || path.join(
      process.env.USERPROFILE || "",
      ".cache",
      "codex-runtimes",
      "codex-primary-runtime",
      "dependencies",
      "node",
      "node_modules",
      "playwright",
      "index.mjs",
    );
    return import(pathToFileURL(runtimeModule).href);
  }
}

async function browserExecutable() {
  const candidates = [
    process.env.FEED_PASSPORT_BROWSER_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Try the next local Chromium browser.
    }
  }
  throw new Error("No local Chromium browser executable was found.");
}

async function waitIdle(page) {
  await page.waitForFunction(() => document.querySelector(".workspace-stage")?.getAttribute("aria-busy") !== "true");
  const alert = page.locator("[role='alert']");
  if (await alert.count()) throw new Error(`Unexpected operation alert: ${await alert.first().innerText()}`);
}

async function openSection(page, id) {
  const button = page.locator(`.desk-tabs button[data-section="${id}"]`);
  assert(await button.count() === 1, `Unknown or duplicate section ${id}`);
  await button.click();
  await button.waitFor({ state: "visible" });
  assert(await button.getAttribute("aria-current") === "page", `${id} did not become active`);
}

async function screenshot(page, name, screenshots) {
  const target = path.join(outputDir, `${name}.png`);
  await page.screenshot({ path: target, fullPage: false, animations: "disabled" });
  screenshots.push(path.relative(projectRoot, target).replaceAll("\\", "/"));
}

async function newScenarioPage(browser, name, options = {}) {
  const events = { console: [], pageErrors: [], failedRequests: [], blockedRequests: [] };
  const context = await browser.newContext({
    viewport: options.viewport || { width: 1440, height: 1024 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    acceptDownloads: true,
  });
  if (options.webmcp) {
    await context.addInitScript(() => {
      const registry = {
        registerTool(definition) {
          window.__webmcpTools ??= new Map();
          window.__webmcpTools.set(definition.name, definition);
          return () => window.__webmcpTools.delete(definition.name);
        },
      };
      Object.defineProperty(document, "modelContext", {
        configurable: true,
        value: registry,
      });
      Object.defineProperty(navigator, "webmcp", {
        configurable: true,
        value: registry,
      });
    });
  }
  const allowedOrigin = new URL(baseUrl).origin;
  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    const parsed = new URL(requestUrl);
    if (parsed.origin === allowedOrigin || ["data:", "blob:"].includes(parsed.protocol)) {
      await route.continue();
      return;
    }
    events.blockedRequests.push(requestUrl);
    await route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (["warning", "error"].includes(message.type())) events.console.push({ type: message.type(), text: message.text() });
  });
  page.on("pageerror", (error) => events.pageErrors.push({ name: error.name, message: error.message }));
  page.on("requestfailed", (request) => {
    if (request.failure()?.errorText !== "net::ERR_BLOCKED_BY_CLIENT") {
      events.failedRequests.push({ url: request.url(), error: request.failure()?.errorText || "unknown" });
    }
  });
  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".passport-workbench").waitFor({ state: "visible" });
  await page.evaluate(async () => document.fonts.ready);
  const fixtureMarker = page.getByText("DETERMINISTIC DEMO", { exact: true }).first();
  await fixtureMarker.waitFor({ state: "attached" });
  assert(await fixtureMarker.textContent() === "DETERMINISTIC DEMO", "Fixture scenario did not stay in deterministic browser mode");
  return { name, context, page, events };
}

async function closeScenario(scenario) {
  assert(scenario.events.console.length === 0, `${scenario.name} emitted console warnings/errors: ${JSON.stringify(scenario.events.console)}`);
  assert(scenario.events.pageErrors.length === 0, `${scenario.name} emitted page errors: ${JSON.stringify(scenario.events.pageErrors)}`);
  assert(scenario.events.failedRequests.length === 0, `${scenario.name} had failed requests: ${JSON.stringify(scenario.events.failedRequests)}`);
  assert(scenario.events.blockedRequests.length === 0, `${scenario.name} attempted external requests: ${JSON.stringify(scenario.events.blockedRequests)}`);
  await scenario.context.close();
}

async function testPassportEditing(browser, report) {
  const scenario = await newScenarioPage(browser, "passport-editing");
  const { page } = scenario;
  await page.getByRole("checkbox", { name: "Yes, issue this passport." }).check();
  await page.getByRole("button", { name: "SEAL PASSPORT ITINERARY" }).click();
  await waitIdle(page);
  assert(await page.getByRole("button", { name: "RESEAL PASSPORT ITINERARY" }).isVisible(), "Passport issuance did not complete");

  await openSection(page, "constitution");
  const independent = page.getByRole("spinbutton", { name: "Independent games percentage", exact: true });
  await independent.fill("0");
  assert(await page.getByRole("button", { name: "STAMP NEW VERSION" }).isDisabled(), "Invalid topic total did not disable stamping");
  await independent.fill("35");
  assert(await page.getByRole("button", { name: "STAMP NEW VERSION" }).isEnabled(), "Valid topic total did not enable stamping");
  await page.getByRole("button", { name: "STAMP NEW VERSION" }).click();
  await waitIdle(page);
  assert(await page.locator(".success-note").isVisible(), "Constitution save notice is missing");

  await openSection(page, "visas");
  await page.locator(".destination-index").getByRole("button", { name: /Instagram.*Not selected/i }).click();
  const instagram = page.locator(".visa-detail-page .visa-seal");
  assert((await instagram.innerText()).includes("INSTAGRAM"), "Instagram manifest did not open");
  assert(await instagram.getAttribute("aria-pressed") === "false", "Instagram unexpectedly started selected");
  await instagram.click();
  assert(await instagram.getAttribute("aria-pressed") === "true", "Instagram visa selection did not toggle");
  await screenshot(page, "passport-editing-visas", report.screenshots);
  report.steps.push("Passport issuance, constitution validation/save, and visa selection passed");
  await closeScenario(scenario);
}

async function testMigration(browser, report) {
  const scenario = await newScenarioPage(browser, "migration");
  const { page } = scenario;
  await openSection(page, "migration");
  await page.getByRole("button", { name: "CAPTURE SOURCE PASSPORT" }).click();
  await waitIdle(page);
  assert(await page.locator(".success-note").isVisible(), "Source capture notice is missing");
  await page.getByRole("button", { name: "PREVIEW TRANSLATION" }).click();
  await waitIdle(page);
  assert(await page.locator(".manifest-actions").isVisible(), "Translation action manifest is missing");
  assert(await page.locator(".translation-loss").isVisible(), "Translation loss disclosure is missing");
  await page.getByRole("button", { name: "APPROVE AND APPLY" }).click();
  await waitIdle(page);
  assert(await page.locator(".approval-block .success-note").isVisible(), "Migration result is missing");
  await screenshot(page, "migration-applied", report.screenshots);
  report.steps.push("Capture, non-mutating preview, translation-loss disclosure, and fixture apply passed");
  await closeScenario(scenario);
}

async function testTemporaryAndCompanion(browser, report) {
  const scenario = await newScenarioPage(browser, "temporary-companion");
  const { page } = scenario;
  await openSection(page, "temporary");
  await page.getByRole("button", { name: "ISSUE TEMPORARY VISA" }).click();
  await waitIdle(page);
  assert(await page.locator(".temporary-visa.active").isVisible(), "Temporary visa was not issued");
  await page.getByRole("button", { name: "Revoke this visa now" }).click();
  await waitIdle(page);
  assert(await page.locator(".temporary-visa.revoked").isVisible(), "Temporary visa was not revoked");

  await openSection(page, "companion");
  await page.getByLabel("Local invitation code").fill("HARBOR-1936");
  await page.getByRole("button", { name: "RECORD MY CONSENT AND ISSUE INVITATION" }).click();
  await waitIdle(page);
  assert(await page.locator(".companion-active").count() === 0, "A companion existed after only the first principal consented");
  const ownerDocket = page.locator(".consent-docket");
  assert(await ownerDocket.isVisible(), "The first principal's consent docket is missing");
  assert((await ownerDocket.innerText()).includes("AWAITING SECOND CONSENT"), "The first consent did not stop at the second-person checkpoint");
  assert((await ownerDocket.innerText()).includes("Continuous · refresh on revision"), "The first consent is not visibly continuous and refreshable");
  assert((await ownerDocket.innerText()).includes("Serendipity"), "The first principal's serendipity selection is missing");
  const secondPrincipal = page.locator(".second-principal-consent");
  assert(await secondPrincipal.isVisible(), "The explicit second-principal checkpoint is missing");
  assert(
    await secondPrincipal.getByText("Visible persona switch · separate deliberate consent", { exact: true }).isVisible(),
    "The local persona handoff is not visible",
  );
  const ownerSelections = page.getByLabel("First local test principal selected fields");
  const partnerSelections = page.getByLabel("Second local test principal selected fields");
  const ownerSerendipity = ownerSelections.getByRole("checkbox", { name: /Serendipity budget/ });
  const partnerSerendipity = partnerSelections.getByRole("checkbox", { name: /Serendipity budget/ });
  assert(await ownerSerendipity.isChecked(), "The first principal did not independently select serendipity");
  assert(await ownerSerendipity.isDisabled(), "The first principal's recorded consent remained editable during handoff");
  assert(!(await partnerSerendipity.isChecked()), "The second principal inherited the first principal's serendipity selection");
  const activate = page.getByRole("button", { name: "SECOND PERSON: CONSENT AND ACTIVATE" });
  assert(await activate.isDisabled(), "The companion could activate before the second principal confirmed consent");
  await screenshot(page, "companion-first-consent", report.screenshots);
  await page.getByRole("checkbox", { name: /I am the second local test principal in this demo/ }).check();
  assert(await activate.isEnabled(), "The second principal's deliberate confirmation did not unlock activation");
  await activate.click();
  await waitIdle(page);
  const activeCompanion = page.locator(".companion-active");
  assert(await activeCompanion.isVisible(), "Two independently consented slices did not create the companion");
  assert((await activeCompanion.innerText()).includes("ACTIVE CONTINUOUS COMPANION"), "The active companion is not visibly continuous");
  assert((await activeCompanion.innerText()).includes("SYNC REVISION 1"), "The initial continuous sync revision is missing");
  assert((await activeCompanion.innerText()).includes("Two active independent slices"), "The two-slice consent state is missing");
  const firstFields = activeCompanion.locator("dl > div").filter({ hasText: "First fields" });
  const secondFields = activeCompanion.locator("dl > div").filter({ hasText: "Second fields" });
  assert((await firstFields.innerText()).includes("Serendipity"), "The first principal's selected serendipity field was lost");
  assert(!(await secondFields.innerText()).includes("Serendipity"), "The second principal received a field they did not select");
  assert((await secondFields.innerText()).includes("Format preferences"), "The second principal's independent format selection is missing");
  const ownerPrincipalId = (await page.locator(".local-principal-note p b").innerText()).trim();
  const partnerPrincipalId = (await page.locator(".blend-ticket .blend-person").nth(1).locator("b").innerText()).trim();
  assert(ownerPrincipalId !== partnerPrincipalId, "Both companion consents were attributed to the same local principal");
  await screenshot(page, "companion-active", report.screenshots);
  await page.getByRole("button", { name: "REVOKE FIRST CONSENT AND STOP SYNC" }).click();
  await waitIdle(page);
  assert(await page.locator(".companion-active").count() === 0, "The continuous companion remained active after consent revocation");
  assert(await page.getByRole("button", { name: "RECORD MY CONSENT AND ISSUE INVITATION" }).isVisible(), "Companion consent did not reset after revocation");
  report.steps.push("Temporary incognito mode plus visible two-person continuous consent, activation, sync revision, and revocation passed");
  await closeScenario(scenario);
}

async function testDriftCreatorsTemplates(browser, report) {
  const scenario = await newScenarioPage(browser, "drift-creators-templates");
  const { page } = scenario;
  await openSection(page, "drift");
  await page.getByRole("button", { name: "RUN FRESH LAB CHECK" }).click();
  await waitIdle(page);
  assert(await page.getByRole("button", { name: "APPROVE LAB CORRECTION" }).isEnabled(), "Fresh drift check did not enable correction");
  await page.getByRole("button", { name: "APPROVE LAB CORRECTION" }).click();
  await waitIdle(page);
  assert(await page.getByText("The fixture correction plan was simulated").isVisible(), "Fixture correction was not shown honestly");
  await page.getByRole("button", { name: "START ALERT-ONLY MONITOR" }).click();
  await waitIdle(page);
  assert(await page.locator(".monitor-active").isVisible(), "Alert-only monitor was not created");
  await page.getByRole("button", { name: "EMERGENCY STOP" }).click();
  await waitIdle(page);

  await openSection(page, "continuity");
  await page.getByLabel("Search passport fixtures").fill("Paper Lab");
  await page.getByRole("button", { name: "Preserve creator" }).click();
  await waitIdle(page);
  assert(await page.getByRole("button", { name: "Preserved" }).isDisabled(), "Creator preservation did not persist in the UI");

  await openSection(page, "templates");
  await page.locator(".template-card").first().getByRole("button", { name: "Load this template" }).click();
  await page.getByRole("heading", { name: "Write the Constitution" }).waitFor({ state: "visible" });
  assert(page.url().endsWith("#constitution"), "Template load did not update browser history");
  report.steps.push("Drift decision/correction/monitor, creator preservation, and template loading passed");
  await closeScenario(scenario);
}

async function testHistoryPortability(browser, report) {
  const scenario = await newScenarioPage(browser, "history-portability");
  const { page } = scenario;
  await openSection(page, "history");
  await page.getByRole("button", { name: "CREATE CHECKPOINT" }).click();
  await waitIdle(page);
  assert(await page.locator(".checkpoint-list article").count() === 1, "Checkpoint was not created");
  await page.getByRole("button", { name: "Restore as new version" }).click();
  await waitIdle(page);
  assert((await page.locator(".success-note").innerText()).toLowerCase().includes("restored"), "Checkpoint restore notice is missing");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "EXPORT PASSPORT" }).click();
  const download = await downloadPromise;
  const exportPath = path.join(outputDir, "exported-passport.json");
  await download.saveAs(exportPath);
  const exported = JSON.parse(await fs.readFile(exportPath, "utf8"));
  assert(exported.format === "feed-passport/v1", "Export did not use feed-passport/v1");
  await page.locator("input[type='file']").setInputFiles(exportPath);
  await waitIdle(page);
  assert((await page.locator(".success-note").innerText()).toLowerCase().includes("import"), "Import notice is missing");
  await screenshot(page, "history-portability", report.screenshots);
  report.steps.push("Checkpoint creation/restore and strict export/import passed");
  await closeScenario(scenario);
}

async function testMission(browser, report, viewport, suffix) {
  const scenario = await newScenarioPage(browser, `mission-${suffix}`, { viewport });
  const { page } = scenario;
  await openSection(page, "agent");
  await page.getByRole("button", { name: "PREVIEW WITHOUT MODEL" }).click();
  await waitIdle(page);
  assert(await page.locator(".mission-consent").isVisible(), "Mission consent checkpoint is missing");
  const run = page.getByRole("button", { name: "RUN LOCALLY" });
  assert(await run.isDisabled(), "Mission run was enabled before approval");
  await page.getByRole("checkbox", { name: /I approve this bounded local mission policy/ }).check();
  assert(await run.isEnabled(), "Mission run did not unlock after approval");

  const contrast = await page.evaluate(() => {
    const parse = (value) => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    const luminance = (rgb) => {
      const values = rgb.map((value) => {
        const channel = value / 255;
        return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
    };
    const element = document.querySelector(".mission-phase-track .phase-pending");
    const foreground = parse(getComputedStyle(element).color);
    const background = parse(getComputedStyle(element.closest(".passport-page")).backgroundColor);
    const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
    return (light + 0.05) / (dark + 0.05);
  });
  assert(contrast >= 4.5, `Pending mission phase contrast ${contrast.toFixed(2)} is below 4.5:1`);
  await screenshot(page, `mission-consent-${suffix}`, report.screenshots);
  await run.click();
  await waitIdle(page);
  await page.locator(".mission-terminal").waitFor({ state: "visible" });
  assert(await page.locator(".iteration-ledger").isVisible(), "Mission iteration ledger is missing");
  assert(await page.getByRole("button", { name: "ROLL BACK RUN" }).isVisible(), "Mission rollback is unavailable");
  await page.getByRole("button", { name: "ROLL BACK RUN" }).click();
  await waitIdle(page);
  assert(await page.getByText("FIXTURE MATCHED", { exact: true }).isVisible(), "Fixture rollback verification is missing");
  await screenshot(page, `mission-rolled-back-${suffix}`, report.screenshots);
  if (viewport.width <= 390) {
    const phaseWidths = await page.locator(".mission-phase-track article").evaluateAll((rows) => rows.map((row) => {
      const content = row.querySelector(":scope > div");
      return {
        row: Math.round(row.getBoundingClientRect().width),
        content: Math.round(content?.getBoundingClientRect().width || 0),
      };
    }));
    assert(phaseWidths.every(({ row, content }) => content >= Math.min(180, row * 0.6)), `Mobile mission phase copy collapsed: ${JSON.stringify(phaseWidths)}`);
    const target = path.join(outputDir, "mission-ledger-mobile-full.png");
    await page.locator(".agent-mission-book").screenshot({ path: target, animations: "disabled" });
    report.screenshots.push(path.relative(projectRoot, target).replaceAll("\\", "/"));
    for (const [name, selector] of [["mission-phases-mobile", ".mission-phase-track"], ["mission-terminal-mobile", ".mission-terminal"]]) {
      const region = path.join(outputDir, `${name}.png`);
      await page.locator(selector).screenshot({ path: region, animations: "disabled" });
      report.screenshots.push(path.relative(projectRoot, region).replaceAll("\\", "/"));
    }
  }
  report.steps.push(`Mission preview, consent, adaptive run, receipts, and verified rollback passed at ${viewport.width}x${viewport.height}`);
  await closeScenario(scenario);
}

async function testMissionCancel(browser, report) {
  const scenario = await newScenarioPage(browser, "mission-cancel");
  const { page } = scenario;
  await openSection(page, "agent");
  await page.getByRole("button", { name: "PREVIEW WITHOUT MODEL" }).click();
  await waitIdle(page);
  await page.getByRole("button", { name: "CANCEL" }).click();
  await waitIdle(page);
  assert(await page.locator(".status-stamp").filter({ hasText: /^cancelled$/i }).first().isVisible(), "Cancelled mission status is missing");
  assert(await page.getByText("BOUNDARY CLOSED", { exact: true }).isVisible(), "Cancelled mission boundary is missing");
  report.steps.push("Mission cancellation before execution passed");
  await closeScenario(scenario);
}

async function testKeyboardFocus(browser, report) {
  const scenario = await newScenarioPage(browser, "keyboard-focus");
  const { page } = scenario;
  await openSection(page, "temporary");
  await page.getByLabel("Purpose").focus();
  await page.keyboard.press("Tab");
  const duration = page.locator(".choice-row").getByRole("radio", { checked: true });
  assert(await duration.evaluate((element) => document.activeElement === element), "Tab did not reach the selected duration radio");
  const durationOutline = await duration.locator("xpath=..").evaluate((label) => getComputedStyle(label).outlineWidth);
  assert(durationOutline !== "0px", "Hidden duration radio has no visible parent focus indicator");
  await page.keyboard.press("Tab");
  const mode = page.getByRole("radio", { name: /Isolated Lab/ });
  assert(await mode.evaluate((element) => document.activeElement === element), "Tab did not reach the selected mode radio");
  const modeOutline = await mode.locator("xpath=..").evaluate((label) => getComputedStyle(label).outlineWidth);
  assert(modeOutline !== "0px", "Mode radio has no visible parent focus indicator");
  await openSection(page, "history");
  await page.getByRole("button", { name: "EXPORT PASSPORT" }).focus();
  await page.keyboard.press("Tab");
  const file = page.locator("input[type='file']");
  assert(await file.evaluate((element) => document.activeElement === element), "Tab did not reach the file import control");
  const fileOutline = await file.locator("xpath=..").evaluate((label) => getComputedStyle(label).outlineWidth);
  assert(fileOutline !== "0px", "Import control has no visible parent focus indicator");
  report.steps.push("Keyboard traversal and visible focus indicators for hidden radios and file input passed");
  await closeScenario(scenario);
}

async function testWebMcp(browser, report) {
  const scenario = await newScenarioPage(browser, "webmcp", { webmcp: true });
  const { page } = scenario;
  await openSection(page, "agent");
  await page.getByText("6 bounded tools", { exact: false }).waitFor({ state: "visible" });
  const result = await page.evaluate(async () => {
    const names = [...window.__webmcpTools.keys()].sort();
    const inspect = await window.__webmcpTools.get("feed_passport.inspect").execute({});
    const preview = await window.__webmcpTools.get("feed_passport.preview_local_agent_mission").execute({
      goal: "Reduce ragebait while preserving selected creators",
      platform: "youtube",
      maxTotalActions: 4,
      maxIterations: 2,
    });
    const mission = await window.__webmcpTools.get("feed_passport.inspect_local_agent_mission").execute({});
    return { names, inspect, preview, mission };
  });
  const expected = [
    "feed_passport.inspect",
    "feed_passport.inspect_local_agent_mission",
    "feed_passport.issue_temporary_visa",
    "feed_passport.open_rollback",
    "feed_passport.preview_local_agent_mission",
    "feed_passport.preview_migration",
  ].sort();
  assert(JSON.stringify(result.names) === JSON.stringify(expected), `Unexpected WebMCP tools: ${JSON.stringify(result.names)}`);
  assert(!result.names.some((name) => /approve|execute|run_mission/.test(name)), "WebMCP exposed an approval or execution tool");
  assert(result.preview.approvalGranted === false && result.preview.accountAccessed === false, "WebMCP mission preview widened authority");
  assert(result.mission.approvalGranted === false && result.mission.accountAccessed === false, "WebMCP mission inspection widened authority");
  report.steps.push("Six WebMCP preview/inspection tools registered with no approval, execution, or account authority");
  await closeScenario(scenario);
}

await fs.mkdir(outputDir, { recursive: true });
const { chromium } = await loadPlaywright();
const executablePath = await browserExecutable();
const browser = await chromium.launch({ executablePath, headless: true, args: ["--disable-background-networking", "--disable-component-update"] });
const report = {
  schema: "feed-passport/browser-flow-qa/v1",
  generatedAt: new Date().toISOString(),
  baseUrl,
  runName,
  fixtureOnly: true,
  externalNetworkAllowed: false,
  steps: [],
  screenshots: [],
  passed: false,
};

try {
  await testPassportEditing(browser, report);
  await testMigration(browser, report);
  await testTemporaryAndCompanion(browser, report);
  await testDriftCreatorsTemplates(browser, report);
  await testHistoryPortability(browser, report);
  await testMission(browser, report, { width: 1440, height: 1024 }, "desktop");
  await testMission(browser, report, { width: 390, height: 844 }, "mobile");
  await testMissionCancel(browser, report);
  await testKeyboardFocus(browser, report);
  await testWebMcp(browser, report);
  report.passed = true;
} catch (error) {
  report.error = { name: error.name, message: error.message, stack: error.stack };
  process.exitCode = 1;
} finally {
  await browser.close();
  const reportPath = path.join(outputDir, "report.json");
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({ report: path.relative(projectRoot, reportPath).replaceAll("\\", "/"), passed: report.passed, steps: report.steps.length, screenshots: report.screenshots.length, error: report.error?.message || null }, null, 2));
}
