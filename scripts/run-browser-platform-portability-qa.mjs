import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { createHmac, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";

const projectRoot = path.resolve(import.meta.dirname, "..");
const apiExecutable = process.platform === "win32"
  ? path.join(projectRoot, "services", "curator", ".venv", "Scripts", "feed-passport-api.exe")
  : path.join(projectRoot, "services", "curator", ".venv", "bin", "feed-passport-api");
const viteExecutable = path.join(projectRoot, "node_modules", "vite", "bin", "vite.js");
const qaScript = path.join(projectRoot, "scripts", "browser-platform-portability-qa.mjs");
const requestedRunName = process.argv.find((value) => value.startsWith("--run="))?.slice(6)
  || `platform-portability-${Date.now()}`;
if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(requestedRunName)) {
  throw new Error("Run name must use 1-64 lowercase letters, digits, underscores, or hyphens.");
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

function waitForExit(child) {
  if (child.spawnFailure) {
    return Promise.resolve({ code: null, signal: null, error: child.spawnFailure });
  }
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    child.once("exit", (code, signal) => finish({ code, signal }));
    child.once("error", (error) => finish({ code: null, signal: null, error }));
  });
}

async function waitForExitBefore(child, label, timeoutMs) {
  let timer;
  const result = await Promise.race([
    waitForExit(child),
    new Promise((resolve) => {
      timer = setTimeout(() => resolve({ timedOut: true }), timeoutMs);
    }),
  ]);
  clearTimeout(timer);
  if (result.timedOut) {
    await stopOwnedProcess(child);
    throw new Error(`${label} exceeded its ${Math.round(timeoutMs / 1000)} second limit`);
  }
  return result;
}

async function waitForUrl(url, child, label, timeoutMs = 30_000, validateResponse = null) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.spawnFailure) throw new Error(`${label} failed to launch: ${child.spawnFailure.message}`);
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`${label} exited before readiness (${child.exitCode ?? child.signalCode})`);
    }
    let response;
    try {
      response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
    } catch {
      // The owned loopback process is still starting.
      await new Promise((resolve) => setTimeout(resolve, 150));
      continue;
    }
    if (response.ok) {
      if (validateResponse && !(await validateResponse(response))) {
        throw new Error(`${label} returned an invalid disposable ownership proof`);
      }
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`${label} did not become ready within 30 seconds`);
}

async function stopOwnedProcess(child) {
  if (!child || child.spawnFailure || child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  const stopped = await Promise.race([
    waitForExit(child).then(() => true),
    new Promise((resolve) => setTimeout(() => resolve(false), 5_000)),
  ]);
  if (!stopped && child.exitCode === null) {
    child.kill("SIGKILL");
    await waitForExit(child);
  }
}

function spawnOwned(command, args, options) {
  const child = spawn(command, args, options);
  child.spawnFailure = null;
  child.on("error", (error) => {
    child.spawnFailure = error;
  });
  return child;
}

function collectLog(child) {
  let content = "";
  for (const stream of [child.stdout, child.stderr]) {
    stream.setEncoding("utf8");
    stream.on("data", (chunk) => {
      content = `${content}${chunk}`.slice(-12_000);
    });
  }
  return () => content;
}

function inheritedNonProjectEnvironment() {
  return Object.fromEntries(Object.entries(process.env).filter(([key]) => {
    const upper = key.toUpperCase();
    return !upper.startsWith("FEED_PASSPORT_")
      && !upper.startsWith("VITE_")
      && !upper.startsWith("AWS_");
  }));
}

function canonicalDatabasePath(value) {
  const normalized = path.resolve(value).replaceAll("\\", "/");
  return process.platform === "win32" ? normalized.toLowerCase() : normalized;
}

let temporaryDirectory = null;
let api = null;
let frontend = null;
let qa = null;
let apiLog = () => "";
let frontendLog = () => "";
let cleanupPromise = null;

function cleanupOwnedResources() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    await Promise.all([
      stopOwnedProcess(qa),
      stopOwnedProcess(frontend),
      stopOwnedProcess(api),
    ]);
    if (temporaryDirectory) {
      await fs.rm(temporaryDirectory, {
        recursive: true,
        force: true,
        maxRetries: 12,
        retryDelay: 250,
      });
    }
  })();
  return cleanupPromise;
}

const signalHandlers = new Map();
for (const [signal, exitCode] of [["SIGINT", 130], ["SIGTERM", 143]]) {
  const handler = () => {
    void cleanupOwnedResources().finally(() => process.exit(exitCode));
  };
  signalHandlers.set(signal, handler);
  process.once(signal, handler);
}

try {
  await Promise.all([fs.access(apiExecutable), fs.access(viteExecutable), fs.access(qaScript)]);
  temporaryDirectory = await fs.mkdtemp(path.join(os.tmpdir(), "feed-passport-browser-platforms-"));
  const databasePath = path.join(temporaryDirectory, "curator.db");
  const markerPath = path.join(temporaryDirectory, "target.json");
  const markerToken = randomUUID();
  const [apiPort, frontendPort] = await Promise.all([reservePort(), reservePort()]);
  const apiUrl = `http://127.0.0.1:${apiPort}`;
  const baseUrl = `http://127.0.0.1:${frontendPort}`;
  const apiInstanceProof = createHmac("sha256", markerToken)
    .update(canonicalDatabasePath(databasePath), "utf8")
    .digest("hex");
  await fs.writeFile(markerPath, `${JSON.stringify({
    schema: "feed-passport/disposable-browser-target/v1",
    token: markerToken,
    base_url: baseUrl,
    api_url: apiUrl,
    owns_database: true,
    api_instance_proof: apiInstanceProof,
  })}\n`, { encoding: "utf8", flag: "wx" });

  api = spawnOwned(apiExecutable, ["--host", "127.0.0.1", "--port", String(apiPort)], {
    cwd: projectRoot,
    env: {
      ...inheritedNonProjectEnvironment(),
      FEED_PASSPORT_DB_PATH: databasePath,
      FEED_PASSPORT_QA_DISPOSABLE_RUN_TOKEN: markerToken,
      FEED_PASSPORT_QA_DISPOSABLE_DATABASE_PATH: databasePath,
      FEED_PASSPORT_AUTH_MODE: "demo",
      FEED_PASSPORT_ALLOW_INSECURE_LOOPBACK_OAUTH: "0",
      FEED_PASSPORT_MODEL_PROVIDER: "disabled",
      FEED_PASSPORT_ENABLE_LOCAL_IMPORT: "1",
      FEED_PASSPORT_ALLOWED_ORIGINS: baseUrl,
    },
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  apiLog = collectLog(api);
  frontend = spawnOwned(
    process.execPath,
    [
      viteExecutable,
      "--configLoader",
      "runner",
      "--host",
      "127.0.0.1",
      "--port",
      String(frontendPort),
      "--strictPort",
    ],
    {
      cwd: projectRoot,
      env: {
        ...inheritedNonProjectEnvironment(),
        VITE_CURATOR_API_URL: apiUrl,
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  frontendLog = collectLog(frontend);

  await Promise.all([
    waitForUrl(
      `${apiUrl}/health`,
      api,
      "disposable API",
      30_000,
      async (response) => {
        const payload = await response.json();
        return payload?.qa_disposable_target?.schema === "feed-passport/disposable-api-target/v1"
          && payload.qa_disposable_target.run_token === markerToken
          && payload.qa_disposable_target.database_binding === apiInstanceProof;
      },
    ),
    waitForUrl(baseUrl, frontend, "disposable frontend"),
  ]);
  qa = spawnOwned(process.execPath, [qaScript, `--run=${requestedRunName}`], {
    cwd: projectRoot,
    env: {
      ...inheritedNonProjectEnvironment(),
      FEED_PASSPORT_BASE_URL: baseUrl,
      FEED_PASSPORT_API_URL: apiUrl,
      FEED_PASSPORT_QA_DISPOSABLE_MARKER_FILE: markerPath,
      FEED_PASSPORT_QA_DISPOSABLE_TOKEN: markerToken,
      ...(process.env.FEED_PASSPORT_BROWSER_EXECUTABLE
        ? { FEED_PASSPORT_BROWSER_EXECUTABLE: process.env.FEED_PASSPORT_BROWSER_EXECUTABLE }
        : {}),
    },
    stdio: "inherit",
    windowsHide: true,
  });
  const result = await waitForExitBefore(qa, "platform portability QA", 180_000);
  if (result.error || result.code !== 0) {
    throw new Error(
      result.error?.message || `platform portability QA exited with code ${result.code ?? "none"}`,
    );
  }
} catch (error) {
  const logs = [
    apiLog().trim() ? `Disposable API log tail:\n${apiLog().trim()}` : "",
    frontendLog().trim() ? `Disposable frontend log tail:\n${frontendLog().trim()}` : "",
  ].filter(Boolean).join("\n\n");
  if (logs) process.stderr.write(`\n${logs}\n`);
  throw error;
} finally {
  for (const [signal, handler] of signalHandlers) process.removeListener(signal, handler);
  await cleanupOwnedResources();
}

process.stdout.write(`${JSON.stringify({
  passed: true,
  target: "owned-disposable-loopback",
  database_removed: true,
  processes_terminated: true,
  run: requestedRunName,
}, null, 2)}\n`);
