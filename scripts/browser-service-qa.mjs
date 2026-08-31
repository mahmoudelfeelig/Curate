import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const projectRoot = path.resolve(import.meta.dirname, "..");
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5175";
const apiUrl = process.env.FEED_PASSPORT_API_URL || "http://127.0.0.1:8002";
const expectLocalModel = process.env.FEED_PASSPORT_EXPECT_LOCAL_MODEL === "1";
const runName = process.argv.find((argument) => argument.startsWith("--run="))?.split("=")[1] || "flow-service";
const outputDir = path.join(projectRoot, "artifacts", "browser-qa", runName);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function mutationFingerprint(snapshot) {
  const normalized = structuredClone(snapshot);
  for (const platform of normalized.platforms || []) {
    if (platform.health) delete platform.health.checked_at;
  }
  return JSON.stringify(normalized);
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
  for (const candidate of [
    process.env.FEED_PASSPORT_BROWSER_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean)) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Try the next local Chromium browser.
    }
  }
  throw new Error("No local Chromium browser executable was found.");
}

async function waitIdle(page, timeout = 30_000) {
  await page.waitForFunction(
    () => document.querySelector(".workspace-stage")?.getAttribute("aria-busy") !== "true",
    null,
    { timeout },
  );
  const alert = page.locator("[role='alert']");
  if (await alert.count()) throw new Error(`Unexpected operation alert: ${await alert.first().innerText()}`);
}

async function apiJson(pathname, options = {}) {
  const response = await fetch(`${apiUrl}${pathname}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  assert(response.ok, `API ${pathname} returned ${response.status}`);
  return response.json();
}

await fs.mkdir(outputDir, { recursive: true });
const report = {
  schema: "feed-passport/browser-service-qa/v2",
  generatedAt: new Date().toISOString(),
  baseUrl,
  apiUrl,
  externalNetworkAllowed: false,
  isolatedDatabase: true,
  expectedPlanner: expectLocalModel ? "local_model" : "deterministic_only",
  events: { console: [], pageErrors: [], failedRequests: [], blockedRequests: [] },
  assertions: [],
  screenshots: [],
  featureClerk: null,
  companion: null,
  mission: null,
  networkEvidence: [],
  passed: false,
  error: null,
};

const { chromium } = await loadPlaywright();
const browser = await chromium.launch({
  executablePath: await browserExecutable(),
  headless: true,
  args: ["--disable-background-networking", "--disable-component-update"],
});
const allowedOrigins = new Set([new URL(baseUrl).origin, new URL(apiUrl).origin]);

async function secureContext(context) {
  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    const parsed = new URL(requestUrl);
    if (allowedOrigins.has(parsed.origin) || ["data:", "blob:"].includes(parsed.protocol)) {
      await route.continue();
      return;
    }
    report.events.blockedRequests.push(requestUrl);
    await route.abort("blockedbyclient");
  });
}

function observePage(page, label) {
  page.on("console", (message) => {
    if (["warning", "error"].includes(message.type())) {
      report.events.console.push({ page: label, type: message.type(), text: message.text() });
    }
  });
  page.on("pageerror", (error) => report.events.pageErrors.push({ page: label, name: error.name, message: error.message }));
  page.on("requestfailed", (request) => {
    if (request.failure()?.errorText !== "net::ERR_BLOCKED_BY_CLIENT") {
      report.events.failedRequests.push({ page: label, url: request.url(), error: request.failure()?.errorText || "unknown" });
    }
  });
}

const context = await browser.newContext({
  viewport: { width: 1440, height: 1024 },
  deviceScaleFactor: 1,
  reducedMotion: "reduce",
});
await secureContext(context);
const page = await context.newPage();
observePage(page, "first-principal");

try {
  let initialPlannerEvidence = null;
  const health = await apiJson("/health");
  assert(health.status === "healthy" && health.scheduler === "active", "Isolated service is not healthy with its scheduler active");
  report.assertions.push("Isolated Curator API healthy with autonomous scheduler active");
  const modelStatus = await apiJson("/api/agent/model/status");
  assert(modelStatus.external_model_calls === false, "Local model status permits external model calls");
  assert(modelStatus.paid_model_calls === false, "Local model status permits paid model calls");
  if (expectLocalModel) {
    assert(modelStatus.configured === true, "Local model provider is not explicitly configured");
    assert(modelStatus.readiness === "ready" && modelStatus.online === true, `Expected a ready local model, received ${JSON.stringify(modelStatus)}`);
    assert(modelStatus.provider === "llamacpp", `Unexpected local model provider ${modelStatus.provider}`);
    assert(modelStatus.mode === "local_only" && modelStatus.endpoint_scope === "loopback_only", "Configured planner is not restricted to loopback");
    report.assertions.push("Loopback-only local model planner reported ready with no external provider fallback");
  } else {
    assert(
      modelStatus.configured === false && modelStatus.online === false && modelStatus.readiness === "disabled",
      `A model was available in the deterministic-only service scenario: ${JSON.stringify(modelStatus)}`,
    );
    report.assertions.push("Deterministic-only scenario kept model inference explicitly disabled");
  }

  await page.goto(`${baseUrl}/#agent`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".passport-workbench").waitFor({ state: "visible" });
  await page.evaluate(async () => document.fonts.ready);
  await page.getByText("LOCAL SERVICE", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("Local Python service", { exact: true }).waitFor({ state: "visible" });
  report.assertions.push("Browser remained in service-backed mode; no fixture fallback occurred");

  await page.locator('.desk-tabs button[data-section="clerk"]').click();
  await page.getByRole("heading", { name: "Feature Clerk" }).waitFor({ state: "visible" });
  if (expectLocalModel) {
    await page.getByText("LOCAL MODEL READY", { exact: true }).waitFor({ state: "visible" });
    const beforeFeatureState = mutationFingerprint(await apiJson("/api/demo"));
    await page.getByLabel("What should your feed do?").fill(
      "Propose an isolated 48-hour Temporary Visa for human-centered agent research. Do not apply it.",
    );
    const featureResponsePromise = page.waitForResponse(
      (response) => new URL(response.url()).pathname === "/api/agent/features/plan"
        && response.request().method() === "POST",
      { timeout: 180_000 },
    );
    await page.getByRole("button", { name: "ASK LOCAL FEATURE CLERK" }).click();
    const featureResponse = await featureResponsePromise;
    assert(featureResponse.status() === 200, `Feature Clerk returned ${featureResponse.status()}`);
    report.networkEvidence.push({ method: "POST", path: "/api/agent/features/plan", status: featureResponse.status() });
    const featureResult = await featureResponse.json();
    await waitIdle(page, 180_000);
    const featureEvidence = featureResult.evidence;
    assert(featureResult.proposal.kind === "temporary_visa", `Unexpected Feature Clerk kind ${featureResult.proposal.kind}`);
    assert(featureResult.proposal.duration_minutes === 2880, "Feature Clerk did not preserve the exact 48-hour duration");
    assert(featureResult.proposal.mode === "isolated", "Feature Clerk did not select isolated Lab mode");
    assert(featureEvidence.authority === "proposal_only" && featureEvidence.mutation_tools_exposed === false, "Feature Clerk exceeded proposal-only authority");
    assert(featureEvidence.proposal_text_source === "deterministic_server_templates", "Feature Clerk displayed model-authored prose");
    assert(featureEvidence.usage?.total_tokens > 0, "Feature Clerk did not record genuine token usage");
    assert(
      JSON.stringify(featureEvidence.tools?.map((item) => item.name)) === JSON.stringify([
        "inspect_selected_passport",
        "inspect_safe_feature_catalog",
        "submit_temporary_visa_proposal",
      ]),
      `Unexpected Feature Clerk tool order: ${JSON.stringify(featureEvidence.tools)}`,
    );
    assert(
      !/alice|approval complete|automatic execution|ranking fidelity/i.test(JSON.stringify(featureResult.proposal)),
      "Feature Clerk proposal leaked identity, authority, or capability-overclaim prose",
    );
    assert(mutationFingerprint(await apiJson("/api/demo")) === beforeFeatureState, "Feature Clerk planning mutated service state");
    const featureProposalScreenshot = path.join(outputDir, "service-feature-clerk-proposal.png");
    await page.screenshot({ path: featureProposalScreenshot, fullPage: false, animations: "disabled" });
    report.screenshots.push(path.relative(projectRoot, featureProposalScreenshot).replaceAll("\\", "/"));
    const applyRequests = [];
    const captureApplyRequest = (request) => {
      const parsed = new URL(request.url());
      if (parsed.origin === new URL(apiUrl).origin && parsed.pathname.startsWith("/api/")) {
        applyRequests.push({ method: request.method(), path: parsed.pathname });
      }
    };
    page.on("request", captureApplyRequest);
    await page.getByRole("button", { name: "APPLY TO DETERMINISTIC DESK" }).click();
    await page.getByRole("heading", { name: "Temporary Visa Office" }).waitFor({ state: "visible" });
    await page.waitForTimeout(100);
    page.off("request", captureApplyRequest);
    assert(applyRequests.length === 0, `Feature Clerk Apply performed API work: ${JSON.stringify(applyRequests)}`);
    assert(await page.locator('[data-proposal-prefill="temporary_visa"]').isVisible(), "Feature Clerk proposal prefill notice is missing");
    assert(await page.locator('[data-exact-duration-minutes="2880"]').isVisible(), "Feature Clerk exact duration was lost during prefill");
    assert(mutationFingerprint(await apiJson("/api/demo")) === beforeFeatureState, "Feature Clerk Apply mutated service state");
    const featureScreenshot = path.join(outputDir, "service-feature-clerk-prefill-only.png");
    await page.screenshot({ path: featureScreenshot, fullPage: false, animations: "disabled" });
    report.screenshots.push(path.relative(projectRoot, featureScreenshot).replaceAll("\\", "/"));
    report.featureClerk = {
      kind: featureResult.proposal.kind,
      durationMinutes: featureResult.proposal.duration_minutes,
      mode: featureResult.proposal.mode,
      toolNames: featureEvidence.tools.map((item) => item.name),
      totalTokens: featureEvidence.usage.total_tokens,
      authority: featureEvidence.authority,
      proposalTextSource: featureEvidence.proposal_text_source,
      applyApiRequests: applyRequests,
      stateUnchanged: true,
    };
    report.assertions.push("Feature Clerk used genuine typed inference, deterministic display text, and proposal-only prefill with unchanged service state");
  } else {
    await page.getByText("DESK LOCKED", { exact: true }).waitFor({ state: "visible" });
    assert(await page.getByRole("button", { name: "ASK LOCAL FEATURE CLERK" }).isDisabled(), "Feature Clerk impersonated AI while the provider was disabled");
    report.featureClerk = { readiness: "disabled", proposalCreated: false };
    report.assertions.push("Feature Clerk stayed locked instead of impersonating AI with a disabled provider");
  }

  await page.locator('.desk-tabs button[data-section="companion"]').click();
  await page.getByRole("heading", { name: "Companion Invitation" }).waitFor({ state: "visible" });
  await page.getByLabel("Local invitation code").fill("SERVICE-PAIR-1936");
  const firstConsentResponsePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/shares" && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "RECORD MY CONSENT AND ISSUE INVITATION" }).click();
  const firstConsentResponse = await firstConsentResponsePromise;
  assert(firstConsentResponse.status() === 201, `First companion consent returned ${firstConsentResponse.status()}`);
  const firstSlice = await firstConsentResponse.json();
  report.networkEvidence.push({ method: "POST", path: "/api/shares", status: firstConsentResponse.status(), principal: "first" });
  await waitIdle(page);
  assert(await page.locator(".companion-active").count() === 0, "A companion existed after only the first principal consented");
  assert(await page.locator(".consent-docket").isVisible(), "The first principal's consent docket is missing");
  assert(
    (await page.locator(".consent-docket").innerText()).includes("Continuous · refresh on revision"),
    "The first principal's continuous refresh scope is not visible",
  );
  const afterFirstConsent = await apiJson("/api/demo");
  assert(afterFirstConsent.companions.length === 0, "The service activated a companion before the second principal consented");
  const persistedFirstSlice = afterFirstConsent.shares.find((item) => item.id === firstSlice.id);
  assert(persistedFirstSlice?.owner_id === "demo-owner", "The first consent was not attributed to the selected local owner");
  assert(persistedFirstSlice.scope === "continuous" && persistedFirstSlice.refresh_on_revision === true, "The first consent is not continuous and refreshable");
  assert(
    JSON.stringify(persistedFirstSlice.target_passport_ids) === JSON.stringify([persistedFirstSlice.passport_id]),
    "The first principal's consent target escaped their own Passport",
  );
  assert(persistedFirstSlice.selected_fields.include_serendipity === true, "The first principal's serendipity selection was not persisted");
  const firstConsentScreenshot = path.join(outputDir, "service-companion-first-consent.png");
  await page.screenshot({ path: firstConsentScreenshot, fullPage: false, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, firstConsentScreenshot).replaceAll("\\", "/"));

  const handoffContext = await browser.newContext({
    viewport: { width: 1440, height: 1024 },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
  });
  await secureContext(handoffContext);
  const handoffPage = await handoffContext.newPage();
  observePage(handoffPage, "second-principal");
  await handoffPage.goto(`${baseUrl}/#companion`, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await handoffPage.locator(".passport-workbench").waitFor({ state: "visible" });
  await handoffPage.getByText("LOCAL SERVICE", { exact: true }).waitFor({ state: "visible" });
  const secondCheckpoint = handoffPage.locator(".second-principal-consent");
  await secondCheckpoint.waitFor({ state: "visible" });
  assert(await handoffPage.locator(".companion-active").count() === 0, "The second browser context observed an active companion before its consent");
  assert(
    await secondCheckpoint.getByText("Visible persona switch · separate deliberate consent", { exact: true }).isVisible(),
    "The second browser context did not expose the deliberate persona handoff",
  );
  const secondSelections = handoffPage.getByLabel("Second local test principal selected fields");
  assert(!(await secondSelections.getByRole("checkbox", { name: /Serendipity budget/ }).isChecked()), "The second principal inherited serendipity without selecting it");
  assert(await secondSelections.getByRole("checkbox", { name: /Format preferences/ }).isChecked(), "The second principal's independent format selection is missing");
  const secondActivate = handoffPage.getByRole("button", { name: "SECOND PERSON: CONSENT AND ACTIVATE" });
  assert(await secondActivate.isDisabled(), "The second browser context could activate without deliberate confirmation");
  await handoffPage.getByRole("checkbox", { name: /I am the second local test principal in this demo/ }).check();
  assert(await secondActivate.isEnabled(), "The second principal's confirmation did not unlock activation");
  const partnerPassportResponsePromise = handoffPage.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/passports" && response.request().method() === "POST",
  );
  const secondConsentResponsePromise = handoffPage.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/shares" && response.request().method() === "POST",
  );
  const companionResponsePromise = handoffPage.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/companions" && response.request().method() === "POST",
  );
  await secondActivate.click();
  const [partnerPassportResponse, secondConsentResponse, companionResponse] = await Promise.all([
    partnerPassportResponsePromise,
    secondConsentResponsePromise,
    companionResponsePromise,
  ]);
  assert(partnerPassportResponse.status() === 201, `Second principal Passport returned ${partnerPassportResponse.status()}`);
  assert(secondConsentResponse.status() === 201, `Second companion consent returned ${secondConsentResponse.status()}`);
  assert(companionResponse.status() === 201, `Continuous companion activation returned ${companionResponse.status()}`);
  const partnerPassport = await partnerPassportResponse.json();
  const secondSlice = await secondConsentResponse.json();
  const createdCompanion = await companionResponse.json();
  report.networkEvidence.push(
    { method: "POST", path: "/api/passports", status: partnerPassportResponse.status(), principal: "second" },
    { method: "POST", path: "/api/shares", status: secondConsentResponse.status(), principal: "second" },
    { method: "POST", path: "/api/companions", status: companionResponse.status() },
  );
  await waitIdle(handoffPage);
  const handoffActive = handoffPage.locator(".companion-active");
  assert(await handoffActive.isVisible(), "Two separately submitted consents did not activate the companion");
  assert((await handoffActive.innerText()).includes("SYNC REVISION 1"), "The initial service sync revision is missing");

  const afterActivation = await apiJson("/api/demo");
  const persistedCompanion = afterActivation.companions.find((item) => item.id === createdCompanion.id);
  assert(persistedCompanion?.scope === "continuous", "The activated companion is not continuous");
  assert(persistedCompanion.refresh_on_revision === true && persistedCompanion.sync_revision === 1, "The activated companion is not refreshable at revision one");
  assert(new Set(persistedCompanion.participant_ids).size === 2, "The companion does not have two distinct local principals");
  assert(new Set(persistedCompanion.consent_ids).size === 2, "The companion does not have two distinct consent records");
  assert(firstSlice.owner_id !== secondSlice.owner_id, "Both consent slices were attributed to the same local principal");
  assert(firstSlice.passport_id !== secondSlice.passport_id, "Both consent slices targeted the same Passport");
  assert(firstSlice.selected_fields.include_serendipity === true, "The first principal's selected serendipity field was lost");
  assert(secondSlice.selected_fields.include_serendipity === false, "The second principal received serendipity without selecting it");
  assert(secondSlice.selected_fields.include_formats === true, "The second principal's selected format field was lost");
  for (const slice of [firstSlice, secondSlice]) {
    assert(slice.scope === "continuous" && slice.refresh_on_revision === true, `Slice ${slice.id} is not continuous and refreshable`);
    assert(JSON.stringify(slice.target_passport_ids) === JSON.stringify([slice.passport_id]), `Slice ${slice.id} escaped its owner's Passport`);
  }
  const activatedScreenshot = path.join(outputDir, "service-companion-second-context-active.png");
  await handoffPage.screenshot({ path: activatedScreenshot, fullPage: false, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, activatedScreenshot).replaceAll("\\", "/"));
  await handoffContext.close();

  const ownerBeforeRefresh = await apiJson(`/api/passports/${encodeURIComponent(firstSlice.passport_id)}`);
  const partnerBeforeRefresh = await apiJson(`/api/passports/${encodeURIComponent(partnerPassport.id)}`);
  const refreshProbe = "browser_companion_refresh_probe";
  await apiJson(`/api/passports/${encodeURIComponent(partnerPassport.id)}`, {
    method: "PATCH",
    body: JSON.stringify({
      actor_id: secondSlice.owner_id,
      changes: {
        format_preferences: {
          ...partnerBeforeRefresh.base.format_preferences,
          [refreshProbe]: 0.93,
        },
      },
    }),
  });
  const afterPartnerRevision = await apiJson("/api/demo");
  const refreshedCompanion = afterPartnerRevision.companions.find((item) => item.id === createdCompanion.id);
  assert(refreshedCompanion.sync_revision === 2, "The partner Passport revision did not advance the companion sync revision");
  assert(
    refreshedCompanion.source_passport_versions[partnerPassport.id] === partnerBeforeRefresh.base.version + 1,
    "The continuous companion did not bind the revised partner Passport version",
  );
  const ownerDuringSync = await apiJson(`/api/passports/${encodeURIComponent(firstSlice.passport_id)}`);
  assert(JSON.stringify(ownerDuringSync.base) === JSON.stringify(ownerBeforeRefresh.base), "Continuous sync mutated the owner's base Passport");
  assert(Number(ownerDuringSync.effective.format_preferences[refreshProbe]) > 0, "The consented partner format revision did not reach the owner's effective projection");

  await page.reload({ waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".companion-active").waitFor({ state: "visible" });
  assert((await page.locator(".companion-active").innerText()).includes("SYNC REVISION 2"), "The first browser context did not hydrate the refreshed sync revision");
  assert(
    await page.getByText("NO BLEND EXISTS", { exact: true }).count() === 0,
    "The hydrated active companion is contradicted by a no-blend boundary",
  );
  assert(
    await page.getByRole("button", { name: "RECORD MY CONSENT AND ISSUE INVITATION" }).count() === 0,
    "The hydrated active companion also exposed a duplicate invitation action",
  );
  const refreshedScreenshot = path.join(outputDir, "service-companion-refreshed-first-context.png");
  await page.screenshot({ path: refreshedScreenshot, fullPage: false, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, refreshedScreenshot).replaceAll("\\", "/"));
  const revokeResponsePromise = page.waitForResponse(
    (response) => /\/api\/shares\/[^/]+\/revoke$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "REVOKE FIRST CONSENT AND STOP SYNC" }).click();
  const revokeResponse = await revokeResponsePromise;
  assert(revokeResponse.status() === 200, `Companion consent revocation returned ${revokeResponse.status()}`);
  report.networkEvidence.push({ method: "POST", path: new URL(revokeResponse.url()).pathname, status: revokeResponse.status() });
  await waitIdle(page);
  assert(await page.locator(".companion-active").count() === 0, "The companion remained active after the first consent was revoked");
  const afterRevocation = await apiJson("/api/demo");
  assert(
    afterRevocation.companions.find((item) => item.id === createdCompanion.id).status === "consent_invalidated",
    "The persisted companion was not invalidated by consent revocation",
  );
  const ownerAfterRevocation = await apiJson(`/api/passports/${encodeURIComponent(firstSlice.passport_id)}`);
  assert(JSON.stringify(ownerAfterRevocation.base) === JSON.stringify(ownerBeforeRefresh.base), "Revocation changed the owner's base Passport");
  assert(!(refreshProbe in ownerAfterRevocation.effective.format_preferences), "The revoked partner field remained in the owner's effective projection");
  report.assertions.push("Two isolated browser contexts recorded independent continuous consent slices before activation");
  report.assertions.push("A partner Passport revision advanced sync revision and refreshed only the effective projection");
  report.assertions.push("Revoking the first consent invalidated the companion and removed the effective partner field");
  report.companion = {
    id: createdCompanion.id,
    statusAfterRevocation: afterRevocation.companions.find((item) => item.id === createdCompanion.id).status,
    scope: persistedCompanion.scope,
    refreshOnRevision: persistedCompanion.refresh_on_revision,
    initialSyncRevision: persistedCompanion.sync_revision,
    refreshedSyncRevision: refreshedCompanion.sync_revision,
    distinctPrincipals: new Set(persistedCompanion.participant_ids).size,
    distinctConsents: new Set(persistedCompanion.consent_ids).size,
    firstSelectedSerendipity: firstSlice.selected_fields.include_serendipity,
    secondSelectedSerendipity: secondSlice.selected_fields.include_serendipity,
    secondSelectedFormats: secondSlice.selected_fields.include_formats,
    twoBrowserContexts: true,
    externalAccounts: 0,
  };

  await page.locator('.desk-tabs button[data-section="agent"]').click();
  await page.getByRole("heading", { name: "Set the Mission" }).waitFor({ state: "visible" });

  const previewButton = expectLocalModel
    ? page.getByRole("button", { name: "ASK LOCAL MODEL TO PLAN" })
    : page.getByRole("button", { name: "PREVIEW WITHOUT MODEL" });
  if (expectLocalModel) {
    await page.getByText("Local planner ready", { exact: true }).waitFor({ state: "visible" });
  }
  const previewPath = expectLocalModel ? "/api/agent/missions/plan" : "/api/agent/missions/preview";
  const previewResponsePromise = page.waitForResponse(
    (response) => new URL(response.url()).pathname === previewPath && response.request().method() === "POST",
    { timeout: expectLocalModel ? 180_000 : 30_000 },
  );
  await previewButton.click();
  const previewResponse = await previewResponsePromise;
  assert(previewResponse.status() === 201, `${previewPath} returned ${previewResponse.status()}`);
  report.networkEvidence.push({ method: "POST", path: previewPath, status: previewResponse.status() });
  await waitIdle(page, expectLocalModel ? 180_000 : 30_000);
  await page.locator(".mission-consent").waitFor({ state: "visible" });
  if (expectLocalModel) {
    const evidence = page.locator(".model-planner-evidence");
    await evidence.waitFor({ state: "visible" });
    await evidence.getByText("POLICY CHECKED", { exact: true }).waitFor({ state: "visible" });
    assert(await evidence.getByText("submit mission proposal", { exact: false }).isVisible(), "Sanitized model tool trace is missing its proposal submission");
    const proposedMission = await apiJson("/api/agent/missions?actor_id=demo-owner");
    assert(proposedMission.length === 1, `Expected one model-planned mission, found ${proposedMission.length}`);
    assert(proposedMission[0].status === "awaiting_approval", "The local model bypassed the consent checkpoint");
    assert(proposedMission[0].approved_at == null && proposedMission[0].approved_by == null, "The model-planned mission was already approved");
    assert(!proposedMission[0].approval_token, "The model-planned mission exposed an approval token");
    const plannerEvidence = proposedMission[0].planner_evidence;
    initialPlannerEvidence = JSON.stringify(plannerEvidence);
    assert(plannerEvidence?.deterministic_validation === "passed", "Persisted model proposal lacks deterministic validation evidence");
    assert(plannerEvidence.endpoint_scope === "loopback_only", "Planner evidence is not loopback-only");
    assert(plannerEvidence.external_model_calls === false && plannerEvidence.paid_model_calls === false, "Planner evidence permits paid or external model calls");
    assert(plannerEvidence.usage?.total_tokens > 0, "Planner evidence does not prove genuine token usage");
    assert(
      JSON.stringify(plannerEvidence.tools?.map((item) => item.name)) === JSON.stringify([
        "inspect_selected_passport",
        "inspect_selected_control_surface",
        "submit_mission_proposal",
      ]),
      `Unexpected model tool order: ${JSON.stringify(plannerEvidence.tools)}`,
    );
    assert((plannerEvidence.admitted_action_types || []).length > 0, "Deterministic compiler admitted no model-requested action family");
    assert(
      (plannerEvidence.admitted_action_types || []).every((item) => (plannerEvidence.requested_action_types || []).includes(item)),
      "The deterministic compiler admitted an action family the model did not request",
    );
    const scope = proposedMission[0].approval_scope;
    assert(scope.actor_id === "demo-owner", "The model changed the locked actor");
    assert(scope.passport_id === proposedMission[0].passport_id && scope.passport_version === proposedMission[0].passport_version, "The model changed the locked Passport identity or version");
    assert(scope.destination_twin === "twin:youtube" && scope.destination_account_id === "destination-new", "The model changed the locked destination scenario");
    assert(JSON.stringify(scope.budget) === JSON.stringify({ max_iterations: 3, per_iteration_actions: 3, total_actions: 6 }), "The model changed the locked action budget");
    assert(scope.acceptance_thresholds.max_total_variation_distance === 0.18, "The model changed the locked acceptance distance");
    assert(scope.min_improvement === 0.02, "The model changed the locked minimum improvement");
    report.assertions.push("Strands local model inspected bounded context, proposed a narrowed mission, and stopped awaiting consent");
  }
  const runButton = page.getByRole("button", { name: "RUN LOCALLY" });
  assert(await runButton.isDisabled(), "Mission execution was enabled before one-time approval");
  report.assertions.push("Execution stayed disabled until one-time mission-bound consent");

  const consentScreenshot = path.join(outputDir, "service-mission-consent.png");
  await page.screenshot({ path: consentScreenshot, fullPage: false, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, consentScreenshot).replaceAll("\\", "/"));

  await page.getByRole("checkbox", { name: "I approve this bounded local mission policy." }).check();
  assert(await runButton.isEnabled(), "Mission execution did not unlock after approval");
  const approvalResponsePromise = page.waitForResponse(
    (response) => /\/api\/agent\/missions\/[^/]+\/approval$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
  );
  const executionResponsePromise = page.waitForResponse(
    (response) => /\/api\/agent\/missions\/[^/]+\/execute$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
  );
  await runButton.click();
  const [approvalResponse, executionResponse] = await Promise.all([approvalResponsePromise, executionResponsePromise]);
  assert(approvalResponse.status() === 200 && executionResponse.status() === 200, "Mission approval or execution did not return 200");
  report.networkEvidence.push(
    { method: "POST", path: new URL(approvalResponse.url()).pathname, status: approvalResponse.status() },
    { method: "POST", path: new URL(executionResponse.url()).pathname, status: executionResponse.status() },
  );
  await waitIdle(page);
  await page.locator(".mission-terminal").waitFor({ state: "visible" });
  assert(await page.locator(".iteration-ledger").isVisible(), "Persisted mission iteration ledger is missing");
  assert(await page.getByRole("button", { name: "ROLL BACK RUN" }).isVisible(), "Separate rollback is unavailable after the terminal mission");

  const missionsBeforeRollback = await apiJson("/api/agent/missions?actor_id=demo-owner");
  assert(missionsBeforeRollback.length === 1, `Expected one persisted mission, found ${missionsBeforeRollback.length}`);
  const completed = missionsBeforeRollback[0];
  assert(["completed", "needs_human"].includes(completed.status), `Mission stopped in unexpected state ${completed.status}`);
  assert((completed.iterations || []).length >= 1, "Persisted mission has no adaptation iteration");
  assert((completed.receipt_ids || []).length >= 1, "Persisted mission has no execution receipt");
  assert((completed.iterations || []).every((item) => item.submitted_action_count <= completed.max_actions_per_iteration), "A mission pass exceeded its per-pass budget");
  assert((completed.iterations || []).reduce((sum, item) => sum + item.submitted_action_count, 0) <= completed.max_total_actions, "The mission exceeded its total action budget");
  if (expectLocalModel) {
    assert(JSON.stringify(completed.planner_evidence) === initialPlannerEvidence, "Planner evidence changed during execution");
  }
  report.assertions.push("Persisted agent observed, planned, acted, re-observed, adapted or stopped, and recorded receipts");

  const completedScreenshot = path.join(outputDir, "service-mission-terminal.png");
  await page.screenshot({ path: completedScreenshot, fullPage: false, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, completedScreenshot).replaceAll("\\", "/"));

  const rollbackApprovalResponsePromise = page.waitForResponse(
    (response) => /\/api\/agent\/missions\/[^/]+\/rollback\/approval$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
  );
  const rollbackResponsePromise = page.waitForResponse(
    (response) => /\/api\/agent\/missions\/[^/]+\/rollback$/.test(new URL(response.url()).pathname) && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "ROLL BACK RUN" }).click();
  const [rollbackApprovalResponse, rollbackResponse] = await Promise.all([rollbackApprovalResponsePromise, rollbackResponsePromise]);
  assert(rollbackApprovalResponse.status() === 200 && rollbackResponse.status() === 200, "Rollback approval or execution did not return 200");
  report.networkEvidence.push(
    { method: "POST", path: new URL(rollbackApprovalResponse.url()).pathname, status: rollbackApprovalResponse.status() },
    { method: "POST", path: new URL(rollbackResponse.url()).pathname, status: rollbackResponse.status() },
  );
  await waitIdle(page);
  await page.getByText("STATE VERIFIED", { exact: true }).waitFor({ state: "visible" });
  const missionsAfterRollback = await apiJson("/api/agent/missions?actor_id=demo-owner");
  const rolledBack = missionsAfterRollback[0];
  assert(rolledBack.status === "rolled_back", `Rollback ended in unexpected state ${rolledBack.status}`);
  assert(rolledBack.rollback?.status === "completed", "Rollback receipt reversal was not completed");
  assert(Number(rolledBack.rollback?.failure_count) === 0, "Rollback recorded failed inverse controls");
  assert(rolledBack.rollback?.verification?.status === "completed", "Rollback verification did not complete");
  assert(rolledBack.rollback?.verification?.state_restored === true, "Rollback did not restore the pre-run local control-state hash");
  if (expectLocalModel) {
    assert(rolledBack.planner_evidence?.usage?.total_tokens > 0, "Planner evidence was lost after execution and rollback");
    assert(JSON.stringify(rolledBack.planner_evidence) === initialPlannerEvidence, "Planner evidence changed during rollback");
  }
  assert(
    JSON.stringify(rolledBack.rollback?.verification?.expected_fingerprint) === JSON.stringify(rolledBack.rollback?.verification?.observed_fingerprint),
    "Rollback expected and observed fingerprints differ",
  );
  report.assertions.push("Separately approved reverse-order rollback restored and verified the pre-run local control state");
  report.mission = {
    id: rolledBack.id,
    status: rolledBack.status,
    terminalStatusBeforeRollback: completed.status,
    terminalReasonBeforeRollback: completed.stop_reason,
    iterations: (rolledBack.iterations || []).length,
    receipts: (rolledBack.receipt_ids || []).length,
    rollbackStatus: rolledBack.rollback.status,
    rollbackFailureCount: rolledBack.rollback.failure_count,
    stateRestored: rolledBack.rollback.verification.state_restored,
    verificationMethod: rolledBack.rollback.verification.method,
    planner: expectLocalModel ? {
      provider: rolledBack.planner_evidence?.provider,
      modelId: rolledBack.planner_evidence?.model_id,
      endpointScope: rolledBack.planner_evidence?.endpoint_scope,
      externalModelCalls: rolledBack.planner_evidence?.external_model_calls,
      paidModelCalls: rolledBack.planner_evidence?.paid_model_calls,
      durationMs: rolledBack.planner_evidence?.duration_ms,
      tokenUsage: rolledBack.planner_evidence?.usage,
      tools: rolledBack.planner_evidence?.tools?.map((item) => item.name) || [],
      requestedActionTypes: rolledBack.planner_evidence?.requested_action_types || [],
      admittedActionTypes: rolledBack.planner_evidence?.admitted_action_types || [],
      rejectedActionTypes: rolledBack.planner_evidence?.rejected_action_types || [],
      deterministicValidation: rolledBack.planner_evidence?.deterministic_validation,
    } : null,
  };

  const rollbackScreenshot = path.join(outputDir, "service-mission-rolled-back.png");
  await page.screenshot({ path: rollbackScreenshot, fullPage: false, animations: "disabled" });
  report.screenshots.push(path.relative(projectRoot, rollbackScreenshot).replaceAll("\\", "/"));

  assert(report.events.console.length === 0, `Console warnings/errors: ${JSON.stringify(report.events.console)}`);
  assert(report.events.pageErrors.length === 0, `Page errors: ${JSON.stringify(report.events.pageErrors)}`);
  assert(report.events.failedRequests.length === 0, `Failed requests: ${JSON.stringify(report.events.failedRequests)}`);
  assert(report.events.blockedRequests.length === 0, `External requests attempted: ${JSON.stringify(report.events.blockedRequests)}`);
  report.assertions.push("Zero console errors, page errors, failed requests, or external network attempts");
  report.passed = true;
} catch (error) {
  report.error = error.stack || error.message;
  process.exitCode = 1;
} finally {
  await context.close();
  await browser.close();
  report.generatedAt = new Date().toISOString();
  await fs.writeFile(path.join(outputDir, "report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
  process.stdout.write(`${JSON.stringify({ report: path.relative(projectRoot, path.join(outputDir, "report.json")).replaceAll("\\", "/"), passed: report.passed, mission: report.mission, error: report.error }, null, 2)}\n`);
}
