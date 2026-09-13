import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const projectRoot = path.resolve(import.meta.dirname, "..");

test("the AgentCore budget guard is fixed at five dollars and plan-first", async () => {
  const script = await fs.readFile(
    path.join(projectRoot, "infra", "agentcore", "scripts", "configure-budget.ps1"),
    "utf8",
  );

  assert.match(script, /\[ValidateRange\("?5"?,\s*"?5"?\)\]\[decimal\]\$LimitUsd\s*=\s*5/);
  assert.match(script, /\[ValidateSet\("Plan",\s*"Apply"\)\]\[string\]\$Mode\s*=\s*"Plan"/);
  assert.match(script, /CREATE FEED PASSPORT FIVE DOLLAR BUDGET/);
  assert.match(script, /NotificationType\s*=\s*"ACTUAL"[\s\S]*Threshold\s*=\s*50/);
  assert.match(script, /NotificationType\s*=\s*"FORECASTED"[\s\S]*Threshold\s*=\s*80/);
  assert.match(script, /NotificationType\s*=\s*"ACTUAL"[\s\S]*Threshold\s*=\s*100/);
  assert.match(script, /not a hard cap/i);
  assert.doesNotMatch(script, /update-budget/);
});

test("the AgentCore bootstrap cannot execute the deployment application", async () => {
  const script = await fs.readFile(
    path.join(projectRoot, "infra", "agentcore", "scripts", "bootstrap.ps1"),
    "utf8",
  );

  assert.match(script, /Invoke-ProjectCdk/);
  assert.match(script, /feed-passport-cdk-bootstrap-/);
  assert.match(script, /StartsWith\([\s\S]*\$temporaryRoot/);
  assert.match(script, /Push-Location \$temporaryDirectory/);
  assert.doesNotMatch(script, /Push-Location \$infraRoot/);
  assert.doesNotMatch(script, /& npx cdk bootstrap/);
});

test("AgentCore scripts use only the active Node npm and project-pinned CDK", async () => {
  const scriptDirectory = path.join(projectRoot, "infra", "agentcore", "scripts");
  const [common, plan, deploy, dryRun] = await Promise.all([
    fs.readFile(path.join(scriptDirectory, "common.ps1"), "utf8"),
    fs.readFile(path.join(scriptDirectory, "plan.ps1"), "utf8"),
    fs.readFile(path.join(scriptDirectory, "deploy.ps1"), "utf8"),
    fs.readFile(path.join(scriptDirectory, "local-dry-run.ps1"), "utf8"),
  ]);

  assert.match(common, /node_modules\\npm\\bin\\npm-cli\.js/);
  assert.match(common, /node_modules\\aws-cdk\\bin\\cdk/);
  for (const script of [plan, deploy, dryRun]) {
    assert.doesNotMatch(script, /&\s+npx\b/);
    assert.doesNotMatch(script, /&\s+npm\b/);
  }
  assert.match(plan, /Invoke-ProjectNpm/);
  assert.match(plan, /Invoke-ProjectCdk/);
  assert.match(deploy, /Invoke-ProjectCdk/);
  assert.match(dryRun, /Invoke-ProjectNpm/);
});

test("AgentCore packaging supports validated Linux ARM64 wheels without Docker", async () => {
  const packageScript = await fs.readFile(
    path.join(projectRoot, "infra", "agentcore", "scripts", "package.ps1"),
    "utf8",
  );
  const preflight = await fs.readFile(
    path.join(projectRoot, "infra", "agentcore", "scripts", "preflight.ps1"),
    "utf8",
  );

  assert.match(packageScript, /PackagingBackend = "PipCrossPlatform"/);
  assert.match(packageScript, /--only-binary=:all:/);
  assert.match(packageScript, /--no-deps/);
  assert.match(packageScript, /--platform manylinux2014_aarch64/);
  assert.match(packageScript, /--python-version 3\.13/);
  assert.match(packageScript, /--abi cp313/);
  assert.match(packageScript, /packaging\\constraints\.txt/);
  assert.match(packageScript, /@\("bin", "Scripts"\)/);
  assert.doesNotMatch(preflight, /@\("node", "npm", "docker", "aws"\)/);
});
