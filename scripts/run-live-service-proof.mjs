import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";

const projectRoot = path.resolve(import.meta.dirname, "..");
const apiExecutable = process.platform === "win32"
  ? path.join(projectRoot, "services", "curator", ".venv", "Scripts", "feed-passport-api.exe")
  : path.join(projectRoot, "services", "curator", ".venv", "bin", "feed-passport-api");
const smokeScript = path.join(projectRoot, "scripts", "live-service-smoke.mjs");

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
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
}

async function waitForHealth(baseUrl, child, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`disposable API exited with code ${child.exitCode}`);
    try {
      const response = await fetch(`${baseUrl}/health`, { signal: AbortSignal.timeout(1_000) });
      if (response.ok) return;
    } catch {
      // The owned loopback process is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("disposable API did not become healthy within 30 seconds");
}

async function runSmoke(baseUrl) {
  const child = spawn(process.execPath, [smokeScript], {
    cwd: projectRoot,
    env: { ...process.env, CURATOR_BASE: baseUrl },
    stdio: "inherit",
    windowsHide: true,
  });
  const result = await waitForExit(child);
  if (result.code !== 0) {
    throw new Error(`live-service smoke exited with code ${result.code ?? "none"} (${result.signal || "no signal"})`);
  }
}

async function stopOwnedProcess(child) {
  if (child.exitCode !== null) return;
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

await fs.access(apiExecutable);
const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "feed-passport-live-proof-"));
const databasePath = path.join(tempDir, "curator.db");
const port = await reservePort();
const baseUrl = `http://127.0.0.1:${port}`;
const serviceEnvironment = {
  ...process.env,
  FEED_PASSPORT_DB_PATH: databasePath,
  FEED_PASSPORT_MODEL_PROVIDER: "disabled",
  FEED_PASSPORT_ALLOWED_ORIGINS: "http://127.0.0.1:1",
};
const service = spawn(apiExecutable, ["--host", "127.0.0.1", "--port", String(port)], {
  cwd: projectRoot,
  env: serviceEnvironment,
  stdio: ["ignore", "pipe", "pipe"],
  windowsHide: true,
});
let serviceLog = "";
for (const stream of [service.stdout, service.stderr]) {
  stream.setEncoding("utf8");
  stream.on("data", (chunk) => {
    serviceLog = `${serviceLog}${chunk}`.slice(-12_000);
  });
}

let passed = false;
try {
  await waitForHealth(baseUrl, service);
  await runSmoke(baseUrl);
  passed = true;
} catch (error) {
  if (serviceLog.trim()) process.stderr.write(`\nDisposable API log tail:\n${serviceLog.trim()}\n`);
  throw error;
} finally {
  await stopOwnedProcess(service);
  await fs.rm(tempDir, {
    recursive: true,
    force: true,
    maxRetries: 12,
    retryDelay: 250,
  });
}

if (passed) {
  process.stdout.write(`${JSON.stringify({
    passed: true,
    service: "owned-disposable-loopback",
    database: "owned-disposable-sqlite",
    modelProvider: "disabled",
    ownedProcessTerminated: true,
  }, null, 2)}\n`);
}
