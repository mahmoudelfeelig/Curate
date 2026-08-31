import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const projectRoot = path.resolve(import.meta.dirname, "..");
const baseUrl = process.env.FEED_PASSPORT_BASE_URL || "http://127.0.0.1:5173";
const passName = process.argv.find((argument) => argument.startsWith("--pass="))?.split("=")[1] || "pass-01";
const outputDir = path.join(projectRoot, "artifacts", "browser-qa", passName);

const viewports = [
  { name: "desktop", width: 1440, height: 1024 },
  { name: "compact-desktop", width: 1024, height: 768 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
  { name: "mobile-200-percent-reflow", width: 195, height: 422, sections: ["overview", "agent"] },
];

const sections = [
  { id: "overview", heading: "Feed Constitution" },
  { id: "constitution", heading: "Write the Constitution" },
  { id: "visas", heading: "Visa Ledger" },
  { id: "migration", heading: "Migration Desk" },
  { id: "temporary", heading: "Temporary Visa Office" },
  { id: "companion", heading: "Companion Invitation" },
  { id: "drift", heading: "Drift Watch" },
  { id: "continuity", heading: "Creator Continuity" },
  { id: "templates", heading: "Policy Template Book" },
  { id: "history", heading: "Action Archive" },
  { id: "clerk", heading: "Feature Clerk" },
  { id: "agent", heading: "Set the Mission" },
];

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

async function findBrowserExecutable() {
  const candidates = [
    process.env.FEED_PASSPORT_BROWSER_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Try the next installed browser.
    }
  }
  throw new Error("No supported local Chromium executable was found.");
}

function normalizeConsoleMessage(message) {
  return {
    type: message.type(),
    text: message.text(),
    location: message.location(),
  };
}

async function inspectPage(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const style = window.getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0 && box.width > 0 && box.height > 0;
    };
    const labelText = (element) => {
      const ariaLabel = element.getAttribute("aria-label")?.trim();
      if (ariaLabel) return ariaLabel;
      const labelledBy = element.getAttribute("aria-labelledby");
      if (labelledBy) {
        const value = labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim() || "").join(" ").trim();
        if (value) return value;
      }
      if ("labels" in element && element.labels?.length) {
        const value = Array.from(element.labels).map((label) => label.textContent?.trim() || "").join(" ").trim();
        if (value) return value;
      }
      return element.textContent?.trim() || element.getAttribute("title")?.trim() || element.getAttribute("placeholder")?.trim() || element.getAttribute("alt")?.trim() || "";
    };

    const interactive = Array.from(document.querySelectorAll("button, a[href], input, select, textarea, summary, [role='button'], [tabindex]"))
      .filter(visible)
      .map((element) => {
        const box = element.getBoundingClientRect();
        const type = element.getAttribute("type") || "";
        const label = ["checkbox", "radio", "file"].includes(type) && "labels" in element
          ? Array.from(element.labels || []).find(visible)
          : null;
        const targetBox = label?.getBoundingClientRect() || box;
        return {
          tag: element.tagName.toLowerCase(),
          type,
          name: labelText(element),
          disabled: Boolean(element.disabled || element.getAttribute("aria-disabled") === "true"),
          x: Math.round(box.x),
          y: Math.round(box.y),
          width: Math.round(targetBox.width),
          height: Math.round(targetBox.height),
          effectiveTarget: label ? "label" : "control",
        };
      });

    const horizontallyClipped = Array.from(document.querySelectorAll("body *"))
      .filter(visible)
      .map((element) => ({ element, box: element.getBoundingClientRect() }))
      .filter(({ element, box }) => !element.closest(".desk-tabs") && (box.left < -1 || box.right > window.innerWidth + 1))
      .slice(0, 40)
      .map(({ element, box }) => ({
        tag: element.tagName.toLowerCase(),
        className: typeof element.className === "string" ? element.className : "",
        text: element.textContent?.trim().replace(/\s+/g, " ").slice(0, 120) || "",
        left: Math.round(box.left),
        right: Math.round(box.right),
        width: Math.round(box.width),
      }));

    const clippedText = Array.from(document.querySelectorAll("p, span, small, b, strong, h1, h2, h3, dt, dd, label, button"))
      .filter(visible)
      .filter((element) => !element.classList.contains("sr-only"))
      .filter((element) => {
        const style = window.getComputedStyle(element);
        const clips = ["hidden", "clip"].includes(style.overflow) || ["hidden", "clip"].includes(style.overflowX) || ["hidden", "clip"].includes(style.overflowY);
        return clips && (element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1);
      })
      .slice(0, 40)
      .map((element) => ({
        tag: element.tagName.toLowerCase(),
        className: typeof element.className === "string" ? element.className : "",
        text: element.textContent?.trim().replace(/\s+/g, " ").slice(0, 120) || "",
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
      }));

    const tinyText = Array.from(document.querySelectorAll("body *"))
      .filter(visible)
      .filter((element) => element.childElementCount === 0 && (element.textContent?.trim().length || 0) > 0)
      .map((element) => ({ element, size: Number.parseFloat(window.getComputedStyle(element).fontSize) }))
      .filter(({ size }) => size < 11)
      .slice(0, 80)
      .map(({ element, size }) => ({
        tag: element.tagName.toLowerCase(),
        className: typeof element.className === "string" ? element.className : "",
        text: element.textContent?.trim().replace(/\s+/g, " ").slice(0, 100) || "",
        fontSize: size,
      }));

    const ids = Array.from(document.querySelectorAll("[id]")).map((element) => element.id).filter(Boolean);
    const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
    const imagesMissingAlt = Array.from(document.images).filter((image) => !image.hasAttribute("alt")).map((image) => image.currentSrc || image.src);
    const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4, h5, h6")).filter(visible).map((heading) => ({
      level: Number(heading.tagName.slice(1)),
      text: heading.textContent?.trim().replace(/\s+/g, " ") || "",
    }));
    const rollback = document.querySelector(".rollback-tab");
    const rollbackBox = rollback && visible(rollback) ? rollback.getBoundingClientRect() : null;
    const rollbackIntersections = rollbackBox
      ? Array.from(document.querySelectorAll("button, a[href], input, select, textarea, summary, label.file-action"))
        .filter((element) => element !== rollback && visible(element))
        .map((element) => ({ element, box: element.getBoundingClientRect() }))
        .filter(({ box }) => rollbackBox.left < box.right && rollbackBox.right > box.left && rollbackBox.top < box.bottom && rollbackBox.bottom > box.top)
        .map(({ element }) => ({
          tag: element.tagName.toLowerCase(),
          className: typeof element.className === "string" ? element.className : "",
          name: labelText(element).slice(0, 120),
        }))
      : [];

    return {
      title: document.title,
      url: window.location.href,
      viewport: { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio },
      document: {
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        clientHeight: document.documentElement.clientHeight,
        scrollHeight: document.documentElement.scrollHeight,
      },
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      horizontallyClipped,
      clippedText,
      unlabeledControls: interactive.filter((control) => !control.name && !control.disabled),
      smallTargets: interactive.filter((control) => !control.disabled && (control.width < 24 || control.height < 24)),
      tinyText,
      duplicateIds,
      imagesMissingAlt,
      headings,
      rollbackIntersections,
      fonts: {
        libreBaskerville: document.fonts.check('16px "Libre Baskerville"'),
        courierPrime: document.fonts.check('16px "Courier Prime"'),
      },
    };
  });
}

async function inspectKeyboardFocus(page, maximumSteps = 36) {
  const focusPath = [];
  for (let index = 0; index < maximumSteps; index += 1) {
    await page.keyboard.press("Tab");
    const state = await page.evaluate(() => {
      const element = document.activeElement;
      if (!element || element === document.body) return null;
      const box = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      const labelledBy = element.getAttribute("aria-labelledby");
      const labelledText = labelledBy
        ? labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent?.trim() || "").join(" ").trim()
        : "";
      const labels = "labels" in element && element.labels?.length
        ? Array.from(element.labels).map((label) => label.textContent?.trim() || "").join(" ").trim()
        : "";
      return {
        tag: element.tagName.toLowerCase(),
        name: element.getAttribute("aria-label")?.trim() || labelledText || labels || element.textContent?.trim().replace(/\s+/g, " ").slice(0, 100) || element.getAttribute("placeholder") || "",
        visible: box.width > 0 && box.height > 0 && box.bottom > 0 && box.top < window.innerHeight,
        outlineStyle: style.outlineStyle,
        outlineWidth: style.outlineWidth,
        boxShadow: style.boxShadow,
      };
    });
    if (state) focusPath.push(state);
  }
  return focusPath;
}

async function captureViewport(browser, viewport) {
  const blockedRequests = [];
  const consoleMessages = [];
  const pageErrors = [];
  const failedRequests = [];
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: 1,
    reducedMotion: "reduce",
    colorScheme: "light",
  });
  await context.route("**/*", async (route) => {
    const requestUrl = route.request().url();
    const parsed = new URL(requestUrl);
    if (["127.0.0.1", "localhost"].includes(parsed.hostname) || ["data:", "blob:"].includes(parsed.protocol)) {
      await route.continue();
      return;
    }
    blockedRequests.push(requestUrl);
    await route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (["warning", "error"].includes(message.type())) consoleMessages.push(normalizeConsoleMessage(message));
  });
  page.on("pageerror", (error) => pageErrors.push({ name: error.name, message: error.message, stack: error.stack }));
  page.on("requestfailed", (request) => {
    const failure = request.failure();
    if (failure?.errorText !== "net::ERR_BLOCKED_BY_CLIENT") {
      failedRequests.push({ url: request.url(), method: request.method(), failure });
    }
  });

  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
  await page.locator(".passport-workbench").waitFor({ state: "visible", timeout: 15_000 });
  await page.evaluate(async () => document.fonts.ready);
  await page.evaluate(() => {
    const originalScrollTo = window.scrollTo.bind(window);
    window.__feedPassportScrollCalls = [];
    window.scrollTo = (options, y) => {
      window.__feedPassportScrollCalls.push(typeof options === "object" ? { ...options } : { left: options, top: y });
      originalScrollTo(options, y);
    };
  });
  const screenshotPath = path.join(outputDir, `overview-${viewport.width}x${viewport.height}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: false, animations: "disabled" });
  const inspection = await inspectPage(page);
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Tab");
  const firstTabStop = await page.evaluate(() => ({
    text: document.activeElement?.textContent?.trim() || "",
    className: typeof document.activeElement?.className === "string" ? document.activeElement.className : "",
  }));
  await page.keyboard.press("Enter");
  const skipTarget = await page.evaluate(() => document.activeElement?.id || "");
  const focusPath = await inspectKeyboardFocus(page);
  const selectedSections = viewport.sections || sections.map((section) => section.id);
  const sectionResults = [{ id: "overview", heading: sections[0].heading, screenshot: path.relative(projectRoot, screenshotPath).replaceAll("\\", "/"), inspection }];
  for (const sectionId of selectedSections.filter((sectionId) => sectionId !== "overview")) {
    const section = sections.find((candidate) => candidate.id === sectionId);
    const sectionIndex = sections.findIndex((candidate) => candidate.id === sectionId);
    await page.locator(".desk-tabs button").nth(sectionIndex).click();
    await page.getByRole("heading", { name: section.heading, exact: true }).first().waitFor({ state: "visible", timeout: 10_000 });
    const sectionScreenshot = path.join(outputDir, `${section.id}-${viewport.width}x${viewport.height}.png`);
    await page.screenshot({ path: sectionScreenshot, fullPage: false, animations: "disabled" });
    sectionResults.push({
      id: section.id,
      heading: section.heading,
      screenshot: path.relative(projectRoot, sectionScreenshot).replaceAll("\\", "/"),
      inspection: await inspectPage(page),
    });
  }
  const navigationHistory = { supported: false, beforeBack: "", afterBack: "", activeHeading: "" };
  if (!viewport.sections) {
    await page.getByRole("button", { name: "Open passport overview" }).click();
    await page.locator(".desk-tabs button").nth(1).click();
    await page.locator(".desk-tabs button").nth(10).click();
    navigationHistory.beforeBack = page.url();
    await page.goBack({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => window.location.hash === "#constitution" && document.querySelector(".desk-tabs [aria-current='page']")?.textContent?.includes("Constitution"));
    navigationHistory.afterBack = page.url();
    navigationHistory.activeHeading = await page.getByRole("heading", { level: 2 }).first().innerText();
    navigationHistory.supported = navigationHistory.afterBack.endsWith("#constitution") && navigationHistory.activeHeading === "Write the Constitution";
  }
  const reducedMotionScrollCalls = await page.evaluate(() => window.__feedPassportScrollCalls || []);
  let ariaSnapshot = "";
  try {
    ariaSnapshot = await page.locator("body").ariaSnapshot({ timeout: 10_000 });
  } catch (error) {
    ariaSnapshot = `ARIA snapshot unavailable: ${error.message}`;
  }
  await fs.writeFile(path.join(outputDir, `aria-${viewport.width}x${viewport.height}.txt`), ariaSnapshot, "utf8");
  await context.close();

  return {
    name: viewport.name,
    width: viewport.width,
    height: viewport.height,
    screenshot: path.relative(projectRoot, screenshotPath).replaceAll("\\", "/"),
    blockedRequests,
    consoleMessages,
    pageErrors,
    failedRequests,
    focusPath,
    firstTabStop,
    skipTarget,
    navigationHistory,
    reducedMotionScrollCalls,
    sections: sectionResults,
    inspection,
  };
}

function gateResults(results) {
  const violations = [];
  const requireEmpty = (items, label) => {
    if (items.length) violations.push(`${label}: ${items.length}`);
  };

  for (const viewport of results) {
    const label = viewport.name;
    requireEmpty(viewport.blockedRequests, `${label} external requests`);
    requireEmpty(viewport.consoleMessages, `${label} console warnings or errors`);
    requireEmpty(viewport.pageErrors, `${label} page errors`);
    requireEmpty(viewport.failedRequests, `${label} failed requests`);
    if (viewport.firstTabStop.className !== "skip-link" || viewport.skipTarget !== "workspace") {
      violations.push(`${label} skip link did not focus the workspace`);
    }
    if (!viewport.focusPath.length || viewport.focusPath.some((item) => !item.visible)) {
      violations.push(`${label} keyboard focus left the visible viewport`);
    }
    if (viewport.focusPath.some((item) => item.outlineStyle === "none" && (!item.boxShadow || item.boxShadow === "none"))) {
      violations.push(`${label} has a keyboard focus target without a visible indicator`);
    }
    if (!viewport.sections || viewport.sections.length === sections.length) {
      if (!viewport.navigationHistory.supported) violations.push(`${label} browser history navigation failed`);
    }
    if (viewport.reducedMotionScrollCalls.some((item) => item.behavior === "smooth")) {
      violations.push(`${label} requested smooth scrolling under reduced motion`);
    }

    for (const section of viewport.sections) {
      const sectionLabel = `${label}/${section.id}`;
      const inspection = section.inspection;
      if (inspection.horizontalOverflow) violations.push(`${sectionLabel} has horizontal document overflow`);
      requireEmpty(inspection.horizontallyClipped, `${sectionLabel} horizontally clipped elements`);
      requireEmpty(inspection.clippedText, `${sectionLabel} clipped text elements`);
      requireEmpty(inspection.unlabeledControls, `${sectionLabel} unlabeled controls`);
      requireEmpty(inspection.smallTargets, `${sectionLabel} undersized controls`);
      requireEmpty(inspection.duplicateIds, `${sectionLabel} duplicate element ids`);
      requireEmpty(inspection.imagesMissingAlt, `${sectionLabel} images missing alt text`);
      requireEmpty(inspection.rollbackIntersections, `${sectionLabel} rollback control intersections`);
      if (!inspection.fonts.libreBaskerville || !inspection.fonts.courierPrime) {
        violations.push(`${sectionLabel} did not load both bundled fonts`);
      }
      const headingLevels = inspection.headings.map((heading) => heading.level);
      if (!headingLevels.includes(1) || !headingLevels.includes(2)) {
        violations.push(`${sectionLabel} is missing its h1 or active-desk h2`);
      }
    }
  }

  return violations;
}

await fs.mkdir(outputDir, { recursive: true });
const { chromium } = await loadPlaywright();
const executablePath = await findBrowserExecutable();
const browser = await chromium.launch({
  executablePath,
  headless: true,
  args: ["--disable-background-networking", "--disable-component-update", "--no-default-browser-check"],
});

try {
  const results = [];
  for (const viewport of viewports) {
    results.push(await captureViewport(browser, viewport));
  }
  const violations = gateResults(results);
  const report = {
    schema: "feed-passport/browser-qa/v1",
    generatedAt: new Date().toISOString(),
    baseUrl,
    passName,
    executablePath,
    browserVersion: browser.version(),
    operatingSystem: `${process.platform}-${process.arch}`,
    nodeVersion: process.version,
    externalNetworkAllowed: false,
    passed: violations.length === 0,
    violations,
    viewports: results,
  };
  const reportPath = path.join(outputDir, "report.json");
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  console.log(JSON.stringify({
    report: path.relative(projectRoot, reportPath).replaceAll("\\", "/"),
    screenshots: results.map((result) => result.screenshot),
    totals: {
      consoleMessages: results.reduce((total, result) => total + result.consoleMessages.length, 0),
      pageErrors: results.reduce((total, result) => total + result.pageErrors.length, 0),
      failedRequests: results.reduce((total, result) => total + result.failedRequests.length, 0),
      horizontalOverflow: results.reduce((total, result) => total + result.sections.filter((section) => section.inspection.horizontalOverflow).length, 0),
      rollbackIntersections: results.reduce((total, result) => total + result.sections.reduce((sectionTotal, section) => sectionTotal + section.inspection.rollbackIntersections.length, 0), 0),
      unlabeledControls: results.reduce((total, result) => total + result.inspection.unlabeledControls.length, 0),
    },
    passed: report.passed,
    violations,
  }, null, 2));
  if (!report.passed) process.exitCode = 1;
} finally {
  await browser.close();
}
