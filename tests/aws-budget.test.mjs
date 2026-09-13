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

  assert.match(script, /node_modules\\aws-cdk\\bin\\cdk/);
  assert.match(script, /feed-passport-cdk-bootstrap-/);
  assert.match(script, /Push-Location \$temporaryDirectory/);
  assert.doesNotMatch(script, /Push-Location \$infraRoot/);
  assert.doesNotMatch(script, /& npx cdk bootstrap/);
});
